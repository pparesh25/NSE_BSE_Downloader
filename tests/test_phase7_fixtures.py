import asyncio

from tests.fixtures.transport_failures import (
    FAILURE_CASES,
    DeterministicFixtureTransport,
    fixture_response,
)


def test_failure_fixture_matrix_is_deterministic_and_offline():
    first = {case: fixture_response(case) for case in FAILURE_CASES}
    second = {case: fixture_response(case) for case in FAILURE_CASES}
    assert first == second
    assert first["html_200"].status == 200
    assert first["empty_zip"].body.startswith(b"PK")
    assert first["429"].retry_after == "7"
    assert first["403"].status == 403
    assert first["404"].status == 404
    assert first["500"].status == 500


def test_fixture_transport_replays_scripted_attempts_without_network():
    transport = DeterministicFixtureTransport([
        fixture_response("timeout"), fixture_response("429"), fixture_response("500")
    ])

    async def run():
        errors = []
        for _ in range(3):
            try:
                errors.append(await transport.request())
            except TimeoutError as error:
                errors.append(str(error))
        return errors

    results = asyncio.run(run())
    assert results[0] == "timeout"
    assert results[1].status == 429
    assert results[1].retry_after == "7"
    assert results[2].status == 500
    assert transport.calls == 3
