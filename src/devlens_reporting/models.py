"""Validated configuration and query contracts. SQL expressions are never accepted."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Column(Model):
    name: Name
    description: str = Field(min_length=1, max_length=2000)
    kind: Literal["text", "number", "date", "boolean"]
    unit: str = Field(default="", max_length=100)


class Relationship(Model):
    column: Name
    target_dataset: Name
    target_column: Name
    cardinality: Literal["many-to-one", "one-to-one", "one-to-many"]
    description: str = Field(min_length=1, max_length=2000)


class Dataset(Model):
    id: Name
    schema_name: Name = "dbo"
    object_name: Name
    description: str = Field(min_length=1, max_length=4000)
    grain: str = Field(min_length=1, max_length=2000)
    business_rules: list[str] = Field(default_factory=list, max_length=50)
    columns: list[Column] = Field(min_length=1, max_length=100)
    relationships: list[Relationship] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def unique_columns(self):
        names = [column.name.casefold() for column in self.columns]
        if len(names) != len(set(names)):
            raise ValueError("Column names must be unique")
        return self


class Catalog(Model):
    version: str = Field(min_length=1, max_length=100)
    datasets: list[Dataset] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def relationships_exist(self):
        datasets = {dataset.id: dataset for dataset in self.datasets}
        if len(datasets) != len(self.datasets):
            raise ValueError("Dataset IDs must be unique")
        for dataset in self.datasets:
            names = {column.name for column in dataset.columns}
            for relation in dataset.relationships:
                target = datasets.get(relation.target_dataset)
                if (relation.column not in names or target is None
                        or relation.target_column not in {c.name for c in target.columns}):
                    raise ValueError("Relationship must reference catalog columns")
        return self


class Connection(Model):
    server: str = Field(default="", max_length=255)
    port: int = Field(default=1433, ge=1, le=65535)
    database: str = Field(default="", max_length=128)
    username: str = Field(default="", max_length=128)
    password_env: Name = "DEVLENS_SQL_PASSWORD"
    encrypt: bool = True
    trust_server_certificate: bool = False
    query_timeout_seconds: int = Field(default=60, ge=1, le=120)


class Participant(Model):
    name: Name
    token_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    datasets: list[Name] = Field(max_length=100)


class State(Model):
    instance: Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,127}$")]
    connection: Connection = Field(default_factory=Connection)
    catalog: Catalog = Field(default_factory=lambda: Catalog(version="unpublished"))
    participants: list[Participant] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_participants(self):
        if len({p.name for p in self.participants}) != len(self.participants):
            raise ValueError("Participant names must be unique")
        if len({p.token_sha256 for p in self.participants}) != len(self.participants):
            raise ValueError("Each participant needs a unique token")
        ids = {d.id for d in self.catalog.datasets}
        if any(set(p.datasets) - ids for p in self.participants):
            raise ValueError("Participant grants must reference published datasets")
        return self


class Filter(Model):
    column: Name
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "is_null", "is_not_null"]
    value: str | int | float | bool | None = None


class Measure(Model):
    column: Name
    function: Literal["sum", "avg", "min", "max", "count"]
    alias: Name


class Query(Model):
    dataset: Name
    fields: list[Name] = Field(default_factory=list, max_length=30)
    filters: list[Filter] = Field(default_factory=list, max_length=20)
    group_by: list[Name] = Field(default_factory=list, max_length=10)
    measures: list[Measure] = Field(default_factory=list, max_length=10)
    order_by: list[Name] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=100, ge=1, le=1000)
