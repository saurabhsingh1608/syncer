from __future__ import annotations

import logging
import pathlib

from google.cloud import bigquery
import pydantic
import sqlalchemy as sa

from cs_tools.sync.base import DatabaseSyncer
from . import sanitize

log = logging.getLogger(__name__)


class BigQuery(DatabaseSyncer):
    """
    Interact with a BigQuery database.
    """

    __manifest_path__ = pathlib.Path(__file__).parent / "MANIFEST.json"
    __syncer_name__ = "bigquery"

    project_name: str
    dataset: str
    credentials_file: pydantic.FilePath

    @property
    def bq(self) -> bigquery.Client:
        """Get the underlying BigQuery client."""
        return self.cnxn.connection._client

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        url = f"bigquery://{self.project_name}/{self.dataset}"
        self._engine = sa.create_engine(url, credentials_path=self.credentials_file)
        print(self.engine)

    def __repr__(self) -> str:
        return f"<BigQuerySyncer >"

    def load(self, tablename: str) -> TableRows:
        """SELECT rows from BigQuery."""
        table = self.metadata.tables[tablename]
        query = table.select().compile(self.engine)
        job   = self.bq.query(query)

        table_rows: TableRows = []

        for row in query_job.result():
            data = table.validated_init(row).model_dump()
            table_rows.append(data)

        return table_rows

    def dump(self, tablename: str, *, data: TableRows) -> None:
        """INSERT rows into BigQuery."""
        if not data:
            log.warning(f"no data to write to syncer {self}")
            return

        table = self.metadata.tables[f"{self.schema_}.{tablename}"]

        if self.load_strategy == "APPEND":
            raise NotImplementedError("coming soon..")

        if self.load_strategy == "TRUNCATE":
            self.session.execute(table.delete())
            raise NotImplementedError("coming soon..")

        if self.load_strategy == "UPSERT":
            # self.merge_into()
            raise NotImplementedError("coming soon..")

        # DEV NOTE: nicholas.cooper@thoughtspot.com
        #
        # Why are we using the underlying BigQuery client?
        #
        # IDK man, Google implemented their SQLAlchemy connector to do individual INSERT
        # queries when provided INSERT MANY orm syntax. I've ETL'd to BigQuery more than
        # often enough to simply just take advantage of the BigQuery client library
        # itself.
        #
        # BigQuery doesn't have a notion of PRIMARY KEY or UNIQUE CONSTRAINT anyway, so
        # transactions and rollback logic is not REALLY possible without more
        # orchestration.
        #
        # https://github.com/googleapis/python-bigquery/blob/132c14bbddfb61ea8bc408bef5e958e21b5b819c/samples/table_insert_rows.py
        # t = self.bq.get_table(f"{self.project_name}.{self.dataset}.{table}")
        # d = sanitize.clean_for_bq(data)

        # cfg = bigquery.LoadJobConfig(schema=t.schema)
        # self.bq.load_table_from_json(d, t, job_config=cfg)
