import polars as pl
import pytest

from services.shared.etl.pipeline import (
    ETLPipeline,
    PipelineStage,
    PipelineReport,
    create_default_pipeline,
    pipeline_from_yaml,
)
from services.shared.etl.data_cleaner import DataCleaner
from services.shared.etl.normalizer import Normalizer
from services.shared.etl.validator import ValidatorPipeline


def make_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "a": [1.0, 2.0, 100.0, 3.0, 4.0],
            "b": [10.0, 20.0, 30.0, 40.0, 5000.0],
            "c": ["x", "y", "z", "w", "v"],
        }
    )


class TestPipelineStage:
    def test_dataclass_defaults(self):
        stage = PipelineStage(name="test", processor="p", stage_type="cleaner")
        assert stage.name == "test"
        assert stage.processor == "p"
        assert stage.stage_type == "cleaner"
        assert stage.on_failure == "raise"
        assert stage.enabled is True

    def test_dataclass_custom(self):
        stage = PipelineStage(
            name="t", processor="p", stage_type="norm",
            on_failure="skip", enabled=False,
        )
        assert stage.on_failure == "skip"
        assert stage.enabled is False


class TestPipelineReport:
    def test_summary_success(self):
        report = PipelineReport(
            pipeline_name="test",
            stages=[
                {"stage": "s1", "type": "cleaner", "input_rows": 10,
                 "output_rows": 8, "duration_ms": 5.0, "status": "ok"},
            ],
            total_duration_ms=5.0,
            input_rows=10,
            output_rows=8,
            all_successful=True,
        )
        s = report.summary()
        assert "SUCCESS" in s
        assert "Pipeline: test" in s
        assert "Rows: 10 -> 8" in s
        assert "[OK]" in s

    def test_summary_failed(self):
        report = PipelineReport(
            pipeline_name="p",
            stages=[
                {"stage": "s1", "type": "cleaner", "input_rows": 5,
                 "output_rows": 0, "duration_ms": 2.0, "status": "error",
                 "error": "bad"},
            ],
            total_duration_ms=2.0,
            input_rows=5,
            output_rows=0,
            all_successful=False,
        )
        s = report.summary()
        assert "FAILED" in s
        assert "[ERR]" in s


class TestETLPipeline:
    def test_add_stage(self):
        p = ETLPipeline()
        p.add_stage("clean", DataCleaner(), "cleaner")
        assert p.get_stage_names() == ["clean"]

    def test_add_stage_returns_self(self):
        p = ETLPipeline()
        ret = p.add_stage("a", DataCleaner(), "cleaner")
        assert ret is p

    def test_remove_stage(self):
        p = ETLPipeline()
        p.add_stage("a", DataCleaner(), "cleaner")
        p.add_stage("b", Normalizer(), "normalizer")
        p.remove_stage("a")
        assert p.get_stage_names() == ["b"]

    def test_remove_stage_returns_self(self):
        p = ETLPipeline()
        p.add_stage("a", DataCleaner(), "cleaner")
        ret = p.remove_stage("a")
        assert ret is p

    def test_get_stage_names_empty(self):
        assert ETLPipeline().get_stage_names() == []

    def test_get_stage_names(self):
        p = ETLPipeline()
        p.add_stage("x", DataCleaner(), "cleaner")
        p.add_stage("y", Normalizer(), "normalizer")
        assert p.get_stage_names() == ["x", "y"]

    def test_enable_disable_stage(self):
        p = ETLPipeline()
        p.add_stage("s", DataCleaner(), "cleaner")
        p.disable_stage("s")
        assert p._stages[0].enabled is False
        p.enable_stage("s")
        assert p._stages[0].enabled is True

    def test_enable_disable_returns_self(self):
        p = ETLPipeline()
        p.add_stage("s", DataCleaner(), "cleaner")
        assert p.enable_stage("s") is p
        assert p.disable_stage("s") is p

    def test_run_empty_pipeline(self):
        df = make_df()
        result, logs = ETLPipeline().run(df)
        assert result is df
        assert logs == []

    def test_run_single_stage(self):
        p = ETLPipeline()
        p.add_stage("clean", DataCleaner(), "cleaner")
        df = make_df()
        result, logs = p.run(df)
        assert len(logs) == 1
        assert logs[0]["stage"] == "clean"
        assert logs[0]["status"] == "ok"
        assert logs[0]["input_rows"] == 5
        assert logs[0]["output_rows"] == 5

    def test_run_disabled_stage_skipped(self):
        p = ETLPipeline()
        p.add_stage("s", DataCleaner(), "cleaner")
        p.disable_stage("s")
        df = make_df()
        result, logs = p.run(df)
        assert logs == []

    def test_run_multiple_stages(self):
        p = ETLPipeline()
        p.add_stage("clean", DataCleaner(), "cleaner")
        p.add_stage("norm", Normalizer(), "normalizer")
        df = make_df()
        result, logs = p.run(df)
        assert len(logs) == 2
        assert all(l["status"] == "ok" for l in logs)

    def test_run_on_failure_raise(self):
        class FailingProcessor:
            def clean(self, df):
                raise ValueError("fail")
            def __call__(self, df):
                raise ValueError("fail")

        p = ETLPipeline()
        p.add_stage("fail", FailingProcessor(), "cleaner")
        with pytest.raises(RuntimeError, match="failed at stage 'fail'"):
            p.run(make_df())

    def test_run_on_failure_skip(self):
        class FailingProcessor:
            def __call__(self, df):
                raise ValueError("skip me")

        p = ETLPipeline()
        p.add_stage("fail", FailingProcessor(), "cleaner", on_failure="skip")
        df = make_df()
        result, logs = p.run(df)
        assert len(logs) == 1
        assert logs[0]["status"] == "error"
        assert logs[0]["output_rows"] == 5  # same as input
        assert result.height == 5  # unchanged

    def test_run_on_failure_warn(self):
        class FailingProcessor:
            def __call__(self, df):
                raise ValueError("warn me")

        p = ETLPipeline()
        p.add_stage("fail", FailingProcessor(), "cleaner", on_failure="warn")
        df = make_df()
        result, logs = p.run(df)
        assert len(logs) == 1
        assert logs[0]["status"] == "error"

    def test_run_with_report(self):
        p = ETLPipeline()
        p.add_stage("clean", DataCleaner(), "cleaner")
        df = make_df()
        result, report = p.run_with_report(df)
        assert isinstance(report, PipelineReport)
        assert report.pipeline_name == "default"
        assert report.input_rows == 5
        assert report.output_rows == 5
        assert report.all_successful is True
        assert len(report.stages) == 1
        assert report.total_duration_ms > 0

    def test_run_with_report_failure(self):
        class FailingProcessor:
            def __call__(self, df):
                raise ValueError("boom")

        p = ETLPipeline()
        p.add_stage("fail", FailingProcessor(), "cleaner", on_failure="skip")
        df = make_df()
        result, report = p.run_with_report(df)
        assert report.all_successful is False

    def test_pipeline_name(self):
        p = ETLPipeline(name="my_pipeline")
        assert p.name == "my_pipeline"


class TestCreateDefaultPipeline:
    def test_default_pipeline(self):
        p = create_default_pipeline()
        assert p.get_stage_names() == ["clean", "normalize", "validate"]

    def test_default_no_validate(self):
        p = create_default_pipeline(validate=False)
        assert p.get_stage_names() == ["clean", "normalize"]

    def test_default_custom_methods(self):
        p = create_default_pipeline(cleaner_method="iqr", normalizer_method="zscore")
        assert p.get_stage_names() == ["clean", "normalize", "validate"]

    def test_default_runs(self):
        p = create_default_pipeline()
        df = make_df()
        result, logs = p.run(df)
        assert len(logs) == 3
        assert all(l["status"] == "ok" for l in logs)


class TestToFromConfig:
    def test_to_config_empty(self):
        cfg = ETLPipeline(name="test").to_config()
        assert cfg == {"name": "test", "stages": []}

    def test_to_config_with_stages(self):
        p = create_default_pipeline()
        cfg = p.to_config()
        assert cfg["name"] == "default"
        assert len(cfg["stages"]) == 3
        for sc in cfg["stages"]:
            assert "name" in sc
            assert "stage_type" in sc
            assert "processor" in sc
            assert "on_failure" in sc
            assert "enabled" in sc

    def test_from_config_roundtrip(self):
        original = create_default_pipeline()
        cfg = original.to_config()
        restored = ETLPipeline.from_config(cfg)
        assert restored.name == original.name
        assert restored.get_stage_names() == original.get_stage_names()
        df = make_df()
        r1, _ = original.run(df.clone())
        r2, _ = restored.run(df.clone())
        assert r1.height == r2.height

    def test_from_config_disabled_stage(self):
        cfg = {
            "name": "test",
            "stages": [
                {"name": "s1", "stage_type": "cleaner", "processor": {"method": "iqr"},
                 "on_failure": "raise", "enabled": False},
                {"name": "s2", "stage_type": "normalizer", "processor": {"method": "zscore"},
                 "on_failure": "raise", "enabled": True},
            ],
        }
        p = ETLPipeline.from_config(cfg)
        assert p.get_stage_names() == ["s1", "s2"]
        df = make_df()
        result, logs = p.run(df)
        assert len(logs) == 1  # s1 was disabled
        assert logs[0]["stage"] == "s2"


class TestPipelineFromYaml:
    def test_pipeline_from_yaml(self, tmp_path):
        yaml_content = """
name: yaml_pipeline
stages:
  - name: clean
    stage_type: cleaner
    processor:
      method: iqr
    on_failure: raise
    enabled: true
  - name: normalize
    stage_type: normalizer
    processor:
      method: zscore
    on_failure: skip
    enabled: true
"""
        yaml_file = tmp_path / "pipeline.yaml"
        yaml_file.write_text(yaml_content)
        p = pipeline_from_yaml(str(yaml_file))
        assert p.name == "yaml_pipeline"
        assert p.get_stage_names() == ["clean", "normalize"]
        df = make_df()
        result, logs = p.run(df)
        assert len(logs) == 2

    def test_pipeline_from_yaml_missing_file(self):
        with pytest.raises(FileNotFoundError):
            pipeline_from_yaml("/tmp/nonexistent.yaml")


class TestIntegration:
    def test_full_pipeline_run(self):
        df = pl.DataFrame(
            {
                "price": [100.0, 200.0, 300.0, 400.0, 50000.0],
                "volume": [1000, 2000, 3000, 4000, 5000],
            }
        )
        p = create_default_pipeline()
        result, report = p.run_with_report(df)
        assert report.all_successful is True
        assert result.height == df.height
        assert "clean" in report.summary()
        assert "normalize" in report.summary()
        assert "validate" in report.summary()

    def test_cleaner_normalizer_only(self):
        df = make_df()
        p = create_default_pipeline(validate=False)
        result, logs = p.run(df)
        assert len(logs) == 2
        assert all(l["status"] == "ok" for l in logs)
