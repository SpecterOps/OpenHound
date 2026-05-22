import glob
import os
import sys

import pytest
from antlr4 import CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener
from openhound.core.models.saved_search import SavedSearch
from pydantic import ValidationError

from .grammar.CypherLexer import CypherLexer
from .grammar.CypherParser import CypherParser

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EXPECTED_CYPHER_FAILURES = {"jamf_query_by_name_error.json"}


class CypherErrorListener(ErrorListener):

    def __init__(self):
        super(CypherErrorListener, self).__init__()

    def syntaxError(self, recognizer, offendingSymbol, line: int, column: int,
                    msg: str, e: Exception) -> None:
        raise ValueError(f"Syntax error at line: {line}, msg: {msg}")


def get_query_files(cypher_dir: str = "tests/test_data/extensions/saved_searches/") -> list:
    if not os.path.exists(cypher_dir):
        return []
    return [
        pytest.param(
            file_path,
            os.path.basename(file_path) in EXPECTED_CYPHER_FAILURES,
            id=os.path.basename(file_path),
        )
        for file_path in sorted(glob.glob(os.path.join(cypher_dir, "*.json"), recursive=True))
    ]


@pytest.mark.parametrize("file_path,should_fail", get_query_files())
def test_cypher_validation(file_path: str, should_fail: bool, request: pytest.FixtureRequest) -> None:
    try:
        # Load the query using the Pydantic schema
        validate_schema = SavedSearch.from_file(file_path)
    except ValidationError as e:
        pytest.fail(f"Pydantic validation failed for {file_path}: {str(e)}", pytrace=False)

    # Save the query content for error reports
    request.node.user_data = {"query": validate_schema.query}

    # Split query into multiple lines in order to remove line comments
    lines = validate_schema.query.splitlines()
    uncomment_query = "\n".join(line.split("//")[0].rstrip() for line in lines)
    uncomment_query = uncomment_query.lstrip("\n")

    # Attempt to load/parse the query using the CypherParser
    lexer = CypherLexer(InputStream(uncomment_query))
    stream = CommonTokenStream(lexer)
    parser = CypherParser(stream)
    parser.addErrorListener(CypherErrorListener())

    if should_fail:
        with pytest.raises(ValueError):
            parser.oC_Cypher()
        return

    # Attempt to parse the query or raise a generic exception
    try:
        parse_query = parser.oC_Cypher()
        assert parse_query.exception is None

    except Exception as e:
        pytest.fail(f"Parsing failed for file {file_path}: {str(e)}", pytrace=False)
