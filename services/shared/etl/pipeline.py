from dataclasses import dataclass, field
from typing import Any, List, Tuple
from datetime import datetime
import time
import logging

import polars as pl

from .data_cleaner import DataCleaner
from .normalizer import Normalizer
from .validator import ValidatorPipeline

logger = logging.getLogger(__name__)


@dataclass
class PipelineStage:
    name: str
    processor: Any
    stage_type: str
    on_failure: str = "raise"
    enabled: bool = True


@dataclass
class PipelineReport:
    pipeline_name: str
    stages: List[dict] = field(default_factory=list)
    total_duration_ms: float = 0.0
    start_time: str = ""
    end_time: str = ""
    input_rows: int = 0
    output_rows: int = 0
    all_successful: bool = True

    def summary(self) -> str:
        status = "SUCCESS" if self.all_successful else "FAILED"
        lines = [
            f"Pipeline: {self.pipeline_name} [{status}]",
            f"  Duration: {self.total_duration_ms:.2f}ms",
            f"  Rows: {self.input_rows} -> {self.output_rows}",
            f"  Stages: {len(self.stages)}",
        ]
        for s in self.stages:
            status_icon = "OK" if s.get("status") == "ok" else "ERR"
            err = f" - {s.get('error', '')}" if s.get("error") else ""
            lines.append(
                f"    [{status_icon}] {s['stage']}({s['type']}) "
                f"{s['input_rows']}->{s['output_rows']} rows "
                f"{s['duration_ms']}ms{err}"
            )
        return "\n".join(lines)


class ETLPipeline:
    def __init__(self, name: str = "default"):
        self.name = name
        self._stages: List[PipelineStage] = []

    def add_stage(
        self,
        name: str,
        processor,
        stage_type: str,
        on_failure: str = "raise",
    ) -> "ETLPipeline":
        self._stages.append(
            PipelineStage(
                name=name,
                processor=processor,
                stage_type=stage_type,
                on_failure=on_failure,
            )
        )
        return self

    def remove_stage(self, name: str) -> "ETLPipeline":
        self._stages = [s for s in self._stages if s.name != name]
        return self

    @staticmethod
    def _call_processor(processor: Any, stage_type: str, df: pl.DataFrame) -> Any:
        if stage_type == "cleaner":
            return processor.clean(df)
        if stage_type == "normalizer":
            numeric_cols = [c for c in df.columns if df[c].dtype.is_numeric()]
            return processor.fit_transform(df, numeric_cols)
        if stage_type == "validator":
            if hasattr(processor, "run_all"):
                processor.run_all(df)
            return df
        raise ValueError(f"Unknown stage_type: {stage_type}")

    def run(self, df: pl.DataFrame) -> Tuple[pl.DataFrame, List[dict]]:
        logs: List[dict] = []
        current = df

        for stage in self._stages:
            if not stage.enabled:
                continue

            input_rows = current.height
            start = time.perf_counter()

            try:
                result = self._call_processor(stage.processor, stage.stage_type, current)

                elapsed = (time.time() - start) * 1000

                if isinstance(result, pl.DataFrame):
                    output_rows = result.height
                    current = result
                else:
                    output_rows = input_rows

                logs.append(
                    {
                        "stage": stage.name,
                        "type": stage.stage_type,
                        "input_rows": input_rows,
                        "output_rows": output_rows,
                        "duration_ms": round(elapsed, 2),
                        "status": "ok",
                        "error": None,
                    }
                )
            except Exception as e:
                elapsed = (time.time() - start) * 1000
                error_msg = str(e)

                if stage.on_failure == "raise":
                    logs.append(
                        {
                            "stage": stage.name,
                            "type": stage.stage_type,
                            "input_rows": input_rows,
                            "output_rows": 0,
                            "duration_ms": round(elapsed, 2),
                            "status": "error",
                            "error": error_msg,
                        }
                    )
                    raise RuntimeError(
                        f"Pipeline '{self.name}' failed at stage '{stage.name}': {error_msg}"
                    ) from e

                if stage.on_failure == "skip":
                    logger.warning(
                        "Stage '%s' failed (skipping): %s", stage.name, error_msg
                    )
                    logs.append(
                        {
                            "stage": stage.name,
                            "type": stage.stage_type,
                            "input_rows": input_rows,
                            "output_rows": input_rows,
                            "duration_ms": round(elapsed, 2),
                            "status": "error",
                            "error": error_msg,
                        }
                    )
                else:
                    logger.warning(
                        "Stage '%s' failed (continuing): %s", stage.name, error_msg
                    )
                    logs.append(
                        {
                            "stage": stage.name,
                            "type": stage.stage_type,
                            "input_rows": input_rows,
                            "output_rows": input_rows,
                            "duration_ms": round(elapsed, 2),
                            "status": "error",
                            "error": error_msg,
                        }
                    )

        return current, logs

    def run_with_report(
        self, df: pl.DataFrame
    ) -> Tuple[pl.DataFrame, PipelineReport]:
        input_rows = df.height
        start_time = datetime.utcnow().isoformat()
        result_df, stage_logs = self.run(df)
        end_time = datetime.utcnow().isoformat()
        total_duration = sum(l.get("duration_ms", 0) for l in stage_logs)
        all_ok = all(l.get("status") == "ok" for l in stage_logs)

        report = PipelineReport(
            pipeline_name=self.name,
            stages=stage_logs,
            total_duration_ms=total_duration,
            start_time=start_time,
            end_time=end_time,
            input_rows=input_rows,
            output_rows=result_df.height,
            all_successful=all_ok,
        )
        return result_df, report

    def get_stage_names(self) -> List[str]:
        return [s.name for s in self._stages]

    def enable_stage(self, name: str) -> "ETLPipeline":
        for s in self._stages:
            if s.name == name:
                s.enabled = True
        return self

    def disable_stage(self, name: str) -> "ETLPipeline":
        for s in self._stages:
            if s.name == name:
                s.enabled = False
        return self

    def to_config(self) -> dict:
        return {
            "name": self.name,
            "stages": [
                {
                    "name": s.name,
                    "stage_type": s.stage_type,
                    "processor": _processor_to_config(s.processor, s.stage_type),
                    "on_failure": s.on_failure,
                    "enabled": s.enabled,
                }
                for s in self._stages
            ],
        }

    @classmethod
    def from_config(cls, config: dict) -> "ETLPipeline":
        pipeline = cls(name=config.get("name", "default"))
        for sc in config.get("stages", []):
            processor = _processor_from_config(sc.get("processor", {}), sc["stage_type"])
            stage = pipeline.add_stage(
                name=sc["name"],
                processor=processor,
                stage_type=sc["stage_type"],
                on_failure=sc.get("on_failure", "raise"),
            )
            if not sc.get("enabled", True):
                pipeline.disable_stage(sc["name"])
        return pipeline


def _processor_to_config(processor: Any, stage_type: str) -> dict:
    if isinstance(processor, DataCleaner):
        return {"method": getattr(processor, "outlier_method", "iqr")}
    if isinstance(processor, Normalizer):
        return {"method": getattr(processor, "_method", "zscore")}
    return {}


def _processor_from_config(cfg: dict, stage_type: str) -> Any:
    if stage_type == "cleaner":
        return DataCleaner(outlier_method=cfg.get("method", "iqr"))
    if stage_type == "normalizer":
        return Normalizer(method=cfg.get("method", "zscore"))
    if stage_type == "validator":
        return ValidatorPipeline()
    raise ValueError(f"Unknown stage_type: {stage_type}")


def create_default_pipeline(
    cleaner_method: str = "iqr",
    normalizer_method: str = "zscore",
    validate: bool = True,
) -> ETLPipeline:
    cleaner = DataCleaner(outlier_method=cleaner_method)
    normalizer = Normalizer(method=normalizer_method)
    pipeline = ETLPipeline(name="default")
    pipeline.add_stage("clean", cleaner, "cleaner")
    pipeline.add_stage("normalize", normalizer, "normalizer")
    if validate:
        pipeline.add_stage("validate", ValidatorPipeline(), "validator")
    return pipeline


def pipeline_from_yaml(yaml_path: str) -> ETLPipeline:
    import yaml
    with open(yaml_path, "r") as f:
        config = yaml.safe_load(f)
    return ETLPipeline.from_config(config)
