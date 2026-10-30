def test_healthz_is_ok_and_leaks_nothing(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_path_is_404(client):
    assert client.get("/does-not-exist").status_code == 404
