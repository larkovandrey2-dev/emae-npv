"""Composition root: the only place that knows every concrete implementation."""

from __future__ import annotations

from dataclasses import dataclass

from .adapters.cashflow_http import CashflowHttpCalculator
from .adapters.curve_parser_http import CurveParserClient
from .adapters.dataset_repo import PostgresDatasetRepository
from .adapters.deal_source import CsvFileDealSource, deal_source
from .adapters.postgres_repo import PostgresRunRepository
from .application.datasets import DatasetService
from .application.pipeline import RunPipeline
from .config import Settings


@dataclass
class Container:
    settings: Settings
    repository: PostgresRunRepository
    calculator: CashflowHttpCalculator
    curves: CurveParserClient
    deals: object
    pipeline: RunPipeline
    datasets: DatasetService


def build(settings: Settings) -> Container:
    repo = PostgresRunRepository(settings.database_url, settings.migrations)
    calculator = CashflowHttpCalculator(settings.calculator_url, indicators_csv=settings.indicators_csv)
    curves = CurveParserClient(settings.curves_url)
    deals = deal_source(settings.deals_source)
    pipeline = RunPipeline(deals, curves, calculator, repo, settings.workers, settings.panel_sample)
    datasets = DatasetService(PostgresDatasetRepository(repo.pool, settings.datasets_dir), deals, CsvFileDealSource)
    return Container(settings, repo, calculator, curves, deals, pipeline, datasets)
