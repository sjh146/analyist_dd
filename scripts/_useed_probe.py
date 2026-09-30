#!/usr/bin/env python3
"""시드별 유니버스 비교 프로브(컨테이너 안에서 실행: PYTHONPATH=/app).

같은 limit 에서 seed 만 바꿨을 때 유니버스가 실제로 달라지는지(=짝 설계가 성립하는지)와
같은 seed 가 재현되는지를 출력한다.
"""
import os

import psycopg2

from app.training.universe import select_training_universe as S

c = psycopg2.connect(
    host=os.environ["POSTGRES_HOST"], port=int(os.environ["POSTGRES_PORT"]),
    dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"])

LIM = int(os.environ.get("LIM", "25"))
u0 = S(c, limit=LIM, min_days=30, seed=0)
u0b = S(c, limit=LIM, min_days=30, seed=0)
u1 = S(c, limit=LIM, min_days=30, seed=1)
u2 = S(c, limit=LIM, min_days=30, seed=2)
print("LIM", LIM)
print("seed0 재현(len, 동일여부):", len(u0), u0 == u0b)
print("seed0 vs seed1 교집합:", len(set(u0) & set(u1)))
print("seed0 vs seed2 교집합:", len(set(u0) & set(u2)))
print("seed0 앞3", u0[:3], "| seed1 앞3", u1[:3])
