from unittest import mock

import pytest


@pytest.mark.django_db
def test_readyz_is_ready_when_the_database_answers(client):
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readyz_reports_unavailable_and_leaks_nothing_when_the_database_is_down(client):
    with mock.patch("foundation.health.connection") as fake:
        fake.cursor.side_effect = Exception("could not connect: host=db password=hunter2")
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert b"hunter2" not in response.content
