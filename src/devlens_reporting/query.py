"""Compile a restricted query tree to SQL Server SELECT and bound values."""
from datetime import date, datetime
from math import isfinite
from .models import Dataset, Query


def quote(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def compile_query(dataset: Dataset, query: Query) -> tuple[str, list]:
    columns = {column.name: column for column in dataset.columns}
    referenced = query.fields + query.group_by + [m.column for m in query.measures]
    referenced += [f.column for f in query.filters]
    if any(name not in columns for name in referenced):
        raise ValueError("Query references an unpublished column")
    if len(set(query.fields)) != len(query.fields) or len(set(query.group_by)) != len(query.group_by):
        raise ValueError("Duplicate fields or grouping columns")
    if query.measures:
        if query.fields and query.fields != query.group_by:
            raise ValueError("For aggregate queries fields must equal group_by or be empty")
        output = list(query.group_by)
        expressions = [quote(name) for name in output]
        for measure in query.measures:
            if measure.alias in output:
                raise ValueError("Output aliases must be unique")
            if measure.function in ("sum", "avg") and columns[measure.column].kind != "number":
                raise ValueError("SUM and AVG require numeric columns")
            function = "COUNT_BIG" if measure.function == "count" else measure.function.upper()
            expressions.append(f"{function}({quote(measure.column)}) AS {quote(measure.alias)}")
            output.append(measure.alias)
    else:
        if query.group_by:
            raise ValueError("Grouping requires measures")
        output = query.fields or list(columns)
        expressions = [quote(name) for name in output]
    if any(name not in output for name in query.order_by):
        raise ValueError("Order columns must appear in the result")
    predicates, values = [], []
    operators = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
    for item in query.filters:
        if item.op in ("is_null", "is_not_null"):
            if item.value is not None:
                raise ValueError("Null filters cannot have a value")
            predicates.append(f"{quote(item.column)} IS {'NOT ' if item.op == 'is_not_null' else ''}NULL")
            continue
        value = item.value
        kind = columns[item.column].kind
        if value is None:
            raise ValueError("Use a null filter for null values")
        if kind == "number" and (type(value) not in (int, float) or not isfinite(value)):
            raise ValueError("Numeric filter requires a finite number")
        if kind == "boolean" and type(value) is not bool:
            raise ValueError("Boolean filter requires true or false")
        if kind == "text" and (not isinstance(value, str) or len(value) > 4000):
            raise ValueError("Text filter requires at most 4000 characters")
        if kind == "date":
            if not isinstance(value, str):
                raise ValueError("Date filter requires ISO date or datetime")
            try:
                value = date.fromisoformat(value) if len(value) == 10 else datetime.fromisoformat(value)
            except ValueError as error:
                raise ValueError("Invalid ISO date") from error
        predicates.append(f"{quote(item.column)} {operators[item.op]} ?")
        values.append(value)
    sql = f"SELECT TOP ({query.limit + 1}) {', '.join(expressions)} FROM {quote(dataset.schema_name)}.{quote(dataset.object_name)}"
    if predicates:
        sql += " WHERE " + " AND ".join(predicates)
    if query.group_by:
        sql += " GROUP BY " + ", ".join(quote(name) for name in query.group_by)
    sql += " ORDER BY " + ", ".join(quote(name) for name in (query.order_by or output))
    return sql, values
