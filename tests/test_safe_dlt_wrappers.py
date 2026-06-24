import logging

from pydantic import BaseModel

from openhound.core.app import OpenHound
from openhound.core.collect import Collector
from openhound.core.progress import Progress


class Computer(BaseModel):
    id: int
    hostname: str


class User(BaseModel):
    id: int
    email: str


class UserDetails(User):
    office: str


def test_dlt_wrapper_pipeline_continues(
    caplog,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / ".dlt"))
    monkeypatch.setattr(
        "openhound.core.collect.logger_override.set_handler", lambda name: None
    )
    caplog.set_level(logging.ERROR, logger="openhound.core.resources")

    app = OpenHound("safe_wrapper_test", "TEST")

    @app.resource(name="computers", columns=Computer)
    def computers():
        yield {"id": 1, "hostname": "DESKTOP-12345"}
        yield {"id": 2, "hostname": "DESKTOP-54321"}
        raise RuntimeError("resource failed after valid rows")

    @app.transformer(name="users", columns=User)
    def users(computer):
        if computer["id"] == 1:
            yield {"id": 10, "email": "someuser@example.org"}
            raise RuntimeError("transformer failed after valid row")

        yield {"id": 20, "email": "someuser2@example.org"}

    @app.transformer(name="user_details", columns=UserDetails)
    def user_details(user):

        @app.defer
        def deferred_child(user_input):
            if user_input["id"] == 1:
                raise RuntimeError("defer failed for parent")

            return {"id": 20, "email": "someuser2@example.org", "office": "Amsterdam"}

        yield deferred_child(user)

    @app.source(name="safe_wrapper_test", max_table_nesting=0)
    def source():
        computers_resource = computers()
        return (
            computers_resource,
            computers_resource | users(),
            computers_resource | user_details(),
        )

    collector = Collector(
        name="safe_wrapper_test",
        output_path=tmp_path / "output",
        progress=Progress.log,
    )

    load_info = collector.run(source())

    assert load_info is not None

    messages = [record.getMessage() for record in caplog.records]
    phases = {getattr(record, "phase", None) for record in caplog.records}

    assert any("resource failed after valid rows" in message for message in messages)
    assert any("transformer failed after valid row" in message for message in messages)
    assert any("defer failed for parent" in message for message in messages)
    assert "resource_iteration" in phases
    assert "defer_execution" in phases
