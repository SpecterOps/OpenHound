from duckdb import DuckDBPyConnection


class LookupManager:
    def __init__(self, client: DuckDBPyConnection, schema: str):
        """Create a DuckDB lookup helper bound to specific schema.

        Args:
            client (DuckDBPyConnection): DuckDB connection used for queries.
            schema (str): Schema name containing lookup tables.
        """
        self.schema = schema
        self.client = client

    def _find_all_objects(self, *args) -> list:
        """Execute a query and return all rows.

        Returns:
            list: Query result rows as a list of tuples.
        """
        self.client.execute(*args)
        results = self.client.fetchall()
        return results

    def _find_single_object(self, *args) -> str | None:
        """Execute a query and return the ID of the matching row

        Returns:
            str | None: The first column (ie. ID) value as a string or None if no result is found
        """
        self.client.execute(*args)
        result = self.client.fetchone()
        return str(result[0]) if result else None
