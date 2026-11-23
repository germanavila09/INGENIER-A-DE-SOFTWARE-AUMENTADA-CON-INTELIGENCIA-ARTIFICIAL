from tests.conftest import INGEST_PAYLOAD, login, hitl_approve_all


def ingest(client, key):
    return client.post("/api/v1/ingest/projects", json=INGEST_PAYLOAD, headers={"X-API-Key": key})


def status(client, code, to, expected_version=None):
    body = {"status": to}
    if expected_version is not None:
        body["expected_version"] = expected_version
    return client.post(f"/api/v1/projects/DEMO/stories/{code}/status", json=body)


def refine_to_refined(client, key, code="DEMO-1"):
    """Lleva una HU de borrador a refinada por el flujo ADK. Requiere sesión de un miembro con escritura."""
    res = status(client, code, "en_refinamiento")
    assert res.status_code == 200, res.text
    pending = client.get("/api/v1/refinements?status=pending", headers={"X-API-Key": key}).json()
    req = next(r for r in pending if r["story_code"] == code)
    client.post(f"/api/v1/refinements/{req['id']}/claim", headers={"X-API-Key": key})
    version = client.get(f"/api/v1/projects/DEMO/stories/{code}").json()["version"]
    res = client.post(
        f"/api/v1/refinements/{req['id']}/result",
        headers={"X-API-Key": key},
        json={"expected_version": version, "acceptance_criteria": "- [ ] Bloquea tras 5 intentos", "notes": "ok"},
    )
    assert res.status_code == 200, res.text
    return req["id"]


def test_ingest_is_idempotent(client, service_key):
    first = ingest(client, service_key)
    assert first.status_code == 201, first.text
    assert first.json()["stories_created"] == 2

    second = ingest(client, service_key)
    assert second.json()["stories_created"] == 0
    assert second.json()["stories_updated"] == 2

    stories = client.get("/api/v1/projects/DEMO/stories", headers={"X-API-Key": service_key}).json()
    assert sorted(s["code"] for s in stories) == ["DEMO-1", "DEMO-2"]
    assert any(s["epic"] == "Autenticación" for s in stories)


def test_ingest_requires_valid_service_key(client):
    assert ingest(client, "").status_code == 401
    assert ingest(client, "spb_falsa").status_code == 401


def test_refinement_cycle(client, service_key):
    ingest(client, service_key)
    login(client, "po@demo.co")

    assert status(client, "DEMO-1", "en_refinamiento").status_code == 200

    pending = client.get("/api/v1/refinements?status=pending", headers={"X-API-Key": service_key}).json()
    assert len(pending) == 1 and pending[0]["story_code"] == "DEMO-1"
    req_id = pending[0]["id"]

    claimed = client.post(f"/api/v1/refinements/{req_id}/claim", headers={"X-API-Key": service_key}).json()
    assert claimed["status"] == "in_progress"
    assert client.post(f"/api/v1/refinements/{req_id}/claim", headers={"X-API-Key": service_key}).status_code == 409

    version = client.get("/api/v1/projects/DEMO/stories/DEMO-1").json()["version"]
    stale = client.post(
        f"/api/v1/refinements/{req_id}/result",
        headers={"X-API-Key": service_key},
        json={"expected_version": version - 1, "title": "Nuevo título"},
    )
    assert stale.status_code == 409

    ok = client.post(
        f"/api/v1/refinements/{req_id}/result",
        headers={"X-API-Key": service_key},
        json={
            "expected_version": version,
            "title": "Iniciar sesión con correo y contraseña",
            "acceptance_criteria": "- [ ] Bloquea tras 5 intentos fallidos",
            "notes": "Se añadió bloqueo",
        },
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "completed"

    story = client.get("/api/v1/projects/DEMO/stories/DEMO-1").json()
    assert story["status"] == "refinada"
    assert story["title"] == "Iniciar sesión con correo y contraseña"
    assert story["acceptance_criteria"] == "- [ ] Bloquea tras 5 intentos fallidos"

    events = [e["event_type"] for e in client.get("/api/v1/projects/DEMO/stories/DEMO-1/events").json()]
    assert "refined" in events and "status_changed" in events

    again = client.post(
        f"/api/v1/refinements/{req_id}/result",
        headers={"X-API-Key": service_key},
        json={"expected_version": version + 1},
    )
    assert again.status_code == 409


def test_cancelling_refinement_marks_request_cancelled(client, service_key):
    ingest(client, service_key)
    login(client, "po@demo.co")
    status(client, "DEMO-1", "en_refinamiento")
    status(client, "DEMO-1", "borrador")
    assert client.get("/api/v1/refinements?status=pending", headers={"X-API-Key": service_key}).json() == []
    cancelled = client.get("/api/v1/refinements?status=cancelled", headers={"X-API-Key": service_key}).json()
    assert len(cancelled) == 1


def test_workflow_permissions_by_role(client, service_key):
    ingest(client, service_key)
    login(client, "po@demo.co")
    refine_to_refined(client, service_key)
    assert status(client, "DEMO-1", "lista").status_code == 200

    # transición inexistente en la matriz
    assert status(client, "DEMO-1", "hecha").status_code == 422

    login(client, "dev@demo.co")
    # transición válida, pero el developer no puede descartar
    assert status(client, "DEMO-1", "descartada").status_code == 403
    assert status(client, "DEMO-1", "en_progreso").status_code == 200
    assert status(client, "DEMO-1", "en_revision").status_code == 200

    login(client, "stake@demo.co")
    assert status(client, "DEMO-1", "en_progreso").status_code == 403


def test_hitl_pipeline_requires_previous_approval(client, service_key):
    ingest(client, service_key)
    login(client, "po@demo.co")

    assert client.post("/api/v1/projects/DEMO/pipeline/2/approve").status_code == 422
    # HITL: sin revisión aprobada de cada HU, la etapa 1 no se aprueba
    blocked = client.post("/api/v1/projects/DEMO/pipeline/1/approve")
    assert blocked.status_code == 422 and "DEMO-1" in blocked.json()["detail"]
    hitl_approve_all(client)
    assert client.post("/api/v1/projects/DEMO/pipeline/1/approve?summary=Revisado").status_code == 200
    assert client.post("/api/v1/projects/DEMO/pipeline/1/approve").status_code == 409
    assert client.post("/api/v1/projects/DEMO/pipeline/2/approve").status_code == 200

    stages = client.get("/api/v1/projects/DEMO/pipeline").json()
    assert [s["status"] for s in stages[:3]] == ["approved", "approved", "pending"]
    assert stages[0]["approved_by"] == "Paula PO"

    # un servicio no puede validar HITL
    assert client.post("/api/v1/projects/DEMO/pipeline/3/approve", headers={"X-API-Key": service_key}).status_code == 422


def test_reports_and_export(client, service_key):
    ingest(client, service_key)
    login(client, "po@demo.co")
    client.patch("/api/v1/projects/DEMO/stories/DEMO-1", json={
        "fp_components": [{"name": "Usuario", "type": "ILF", "det": 5, "ret": 1}], "priority_rank": 1,
    })

    summary = client.get("/api/v1/projects/DEMO/reports/summary").json()
    assert summary["total_stories"] == 2
    assert summary["total_function_points"] == 7 and summary["unsized_stories"] == 1
    assert summary["by_status"]["borrador"]["count"] == 2

    csv = client.get("/api/v1/projects/DEMO/export?format=csv")
    assert csv.status_code == 200
    assert csv.headers["content-type"].startswith("text/csv")
    assert "DEMO-1" in csv.text and "code,epic,title" in csv.text

    data = client.get("/api/v1/projects/DEMO/export?format=json").json()
    assert data["stories"][0]["code"] == "DEMO-1"


def test_stakeholder_is_read_only_and_non_member_gets_404(client, service_key, make_user):
    ingest(client, service_key)
    make_user("externo@demo.co", "Externo")

    login(client, "stake@demo.co")
    assert client.get("/api/v1/projects/DEMO/stories").status_code == 200
    assert client.post("/api/v1/projects/DEMO/stories", json={"title": "Nueva HU"}).status_code == 403

    login(client, "externo@demo.co")
    assert client.get("/api/v1/projects/DEMO").status_code == 404


def test_login_rejects_bad_credentials(client):
    res = client.post("/api/v1/auth/login", json={"email": "nadie@x.co", "password": "x"})
    assert res.status_code == 401


def test_adk_ingests_assignee_by_email_and_moves_status(client, service_key):
    """Flujo ADK completo: crea proyecto con miembros, HU asignadas por correo y cambia estado con la API key."""
    ingest(client, service_key)
    payload = {
        "key": "PAGO",
        "name": "Pagos",
        "members": [{"email": "dev@demo.co", "role": "developer"}, {"email": "po@demo.co", "role": "product_owner"}],
        "epics": [{"title": "Checkout", "stories": [
            {"external_id": "r1-hu1", "title": "Pagar con tarjeta", "type": "HU_IA", "assignee_email": "dev@demo.co"}
        ]}],
    }
    res = client.post("/api/v1/ingest/projects", json=payload, headers={"X-API-Key": service_key})
    assert res.status_code == 201, res.text
    story = client.get("/api/v1/projects/PAGO/stories/PAGO-1", headers={"X-API-Key": service_key}).json()
    assert story["assignee"]["email"] == "dev@demo.co"

    moved = client.post("/api/v1/projects/PAGO/stories/PAGO-1/status",
                        headers={"X-API-Key": service_key}, json={"status": "en_refinamiento"})
    assert moved.status_code == 200, moved.text

    bad = client.post("/api/v1/projects/PAGO/stories/PAGO-1/assign",
                      headers={"X-API-Key": service_key}, json={"assignee_email": "externo@x.co"})
    assert bad.status_code == 422
    ok = client.post("/api/v1/projects/PAGO/stories/PAGO-1/assign",
                     headers={"X-API-Key": service_key}, json={"assignee_email": "po@demo.co"})
    assert ok.json()["assignee"]["email"] == "po@demo.co"
    cleared = client.post("/api/v1/projects/PAGO/stories/PAGO-1/assign",
                          headers={"X-API-Key": service_key}, json={"assignee_email": ""})
    assert cleared.json()["assignee"] is None


def test_acceptance_criteria_must_be_markdown_string(client, service_key):
    ingest(client, service_key)
    login(client, "po@demo.co")
    res = client.patch("/api/v1/projects/DEMO/stories/DEMO-1", json={"acceptance_criteria": ["lista", "no"]})
    assert res.status_code == 422


def test_acceptance_criteria_is_stored_as_markdown(client, service_key):
    ingest(client, service_key)
    login(client, "po@demo.co")
    md = "## Escenario\n\n- [ ] **Given** tarjeta rechazada\n- [x] *When* reintento\n"
    res = client.patch("/api/v1/projects/DEMO/stories/DEMO-1", json={"acceptance_criteria": md})
    assert res.status_code == 200
    assert res.json()["acceptance_criteria"] == md
