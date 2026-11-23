from tests.conftest import INGEST_PAYLOAD, PASSWORD, login, hitl_approve_all


def web_login(client, email):
    res = client.post("/login", data={"email": email, "password": PASSWORD}, follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/projects"


def test_anonymous_is_redirected_to_login(client):
    res = client.get("/projects", follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/login"


def test_invalid_web_login_shows_error(client, service_key):
    res = client.post("/login", data={"email": "po@demo.co", "password": "mal"})
    assert res.status_code == 200
    assert "Credenciales inválidas" in res.text


def test_pages_render_for_member(client, service_key):
    from tests.conftest import INGEST_PAYLOAD as payload  # noqa: F401

    client.post("/api/v1/ingest/projects", json=INGEST_PAYLOAD, headers={"X-API-Key": service_key})
    web_login(client, "po@demo.co")

    assert client.get("/projects").status_code == 200
    assert client.get("/p/DEMO").status_code == 200
    assert client.get("/p/DEMO/backlog").status_code == 200
    assert client.get("/p/DEMO/backlog?status=borrador&sort=title").status_code == 200
    assert client.get("/p/DEMO/board").status_code == 200
    assert client.get("/p/DEMO/reports").status_code == 200
    assert client.get("/p/DEMO/members").status_code == 200

    story = client.get("/p/DEMO/stories/DEMO-1")
    assert story.status_code == 200
    assert "Iniciar sesión" in story.text


def test_web_forms_create_and_transition_story(client, service_key):
    client.post("/api/v1/ingest/projects", json=INGEST_PAYLOAD, headers={"X-API-Key": service_key})
    web_login(client, "po@demo.co")

    created = client.post(
        "/p/DEMO/stories",
        data={"title": "Exportar a CSV", "epic": "Reportes", "role_": "PO", "criteria": "- [ ] Incluye HU\n- [ ] Incluye épica"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    assert created.headers["location"] == "/p/DEMO/stories/DEMO-3"

    moved = client.post("/p/DEMO/stories/DEMO-3/status", data={"status": "en_refinamiento"}, follow_redirects=False)
    assert moved.status_code == 303
    api_story = client.get("/api/v1/projects/DEMO/stories/DEMO-3").json()
    assert api_story["status"] == "en_refinamiento"
    assert api_story["acceptance_criteria"] == "- [ ] Incluye HU\n- [ ] Incluye épica"

    bad = client.post("/p/DEMO/stories/DEMO-3/status", data={"status": "hecha"}, follow_redirects=False)
    assert bad.status_code == 303  # redirige con mensaje de error
    assert client.get("/api/v1/projects/DEMO/stories/DEMO-3").json()["status"] == "en_refinamiento"


def test_web_edit_story_and_approve_stage(client, service_key):
    client.post("/api/v1/ingest/projects", json=INGEST_PAYLOAD, headers={"X-API-Key": service_key})
    web_login(client, "po@demo.co")

    res = client.post(
        "/p/DEMO/stories/DEMO-1/edit",
        data={"title": "Iniciar sesión (editada)", "epic": "Autenticación", "priority_rank": "2",
              "criteria": "Muestra error"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    story = client.get("/api/v1/projects/DEMO/stories/DEMO-1").json()
    assert story["title"] == "Iniciar sesión (editada)"
    assert story["priority_rank"] == 2

    hitl_approve_all(client)
    res = client.post("/p/DEMO/pipeline/1/approve", data={"summary": "ok"}, follow_redirects=False)
    assert res.status_code == 303
    stages = client.get("/api/v1/projects/DEMO/pipeline").json()
    assert stages[0]["status"] == "approved"


def test_developer_cannot_edit_definition(client, service_key):
    client.post("/api/v1/ingest/projects", json=INGEST_PAYLOAD, headers={"X-API-Key": service_key})
    web_login(client, "dev@demo.co")
    client.post("/p/DEMO/stories/DEMO-1/edit", data={"title": "Hackeado por dev"}, follow_redirects=False)
    assert client.get("/api/v1/projects/DEMO/stories/DEMO-1").json()["title"] == "Iniciar sesión"


def test_admin_page_forbidden_for_regular_user_and_key_creation(client, service_key, make_user):
    client.post("/api/v1/ingest/projects", json=INGEST_PAYLOAD, headers={"X-API-Key": service_key})
    web_login(client, "po@demo.co")
    assert client.get("/admin").status_code == 403

    make_user("root@demo.co", "Root", admin=True)
    login(client, "root@demo.co")
    page = client.get("/admin")
    assert page.status_code == 200

    created = client.post("/admin/api-keys", data={"name": "adk-clasificador"}, follow_redirects=False)
    assert created.status_code == 303
    shown = client.get("/admin")
    assert "spb_" in shown.text and "adk-clasificador" in shown.text


def test_markdown_criteria_render_escaped_html(client, service_key):
    client.post("/api/v1/ingest/projects", json=INGEST_PAYLOAD, headers={"X-API-Key": service_key})
    web_login(client, "po@demo.co")
    client.patch(
        "/api/v1/projects/DEMO/stories/DEMO-1",
        json={"acceptance_criteria": "- [ ] **negrita** y <script>alert(1)</script>"},
    )
    page = client.get("/p/DEMO/stories/DEMO-1").text
    assert "<strong>negrita</strong>" in page
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
