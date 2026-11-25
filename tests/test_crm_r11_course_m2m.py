"""CRM-R11: Course ↔ Project M2M polish — one course on many projects; Project name visible."""
import os
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from sqlalchemy import select

from app.crm_models import ProjectCourse

ROOT = Path(__file__).resolve().parents[1]

pytest_plugins = ("test_operational", "test_crm")


def _second_project(crm, name="Менторство пакет"):
    created = crm.client.post("/api/projects", json={"name": name, "goal": "Пакет с несколькими курсами"})
    assert created.status_code == 200, created.text
    return created.json()["id"]


def test_course_binds_to_many_projects_with_names(crm):
    """Same LMS course_id on two Projects; staff see both project names."""
    pid_b = _second_project(crm)
    bind_a = crm.client.post(
        "/api/crm/projects/p/courses",
        json={"course_id": "olat-shared-1", "label": "Базовый"},
    )
    assert bind_a.status_code == 200, bind_a.text
    body_a = bind_a.json()
    assert body_a["course_id"] == "olat-shared-1"
    assert body_a["project_id"] == "p"
    assert body_a["project_name"]  # fixture project has a name
    assert any(x["project_id"] == "p" for x in body_a["projects"])

    bind_b = crm.client.post(
        f"/api/crm/projects/{pid_b}/courses",
        json={"course_id": "olat-shared-1", "label": "В пакете"},
    )
    assert bind_b.status_code == 200, bind_b.text
    body_b = bind_b.json()
    assert body_b["project_id"] == pid_b
    names = {x["project_name"] for x in body_b["projects"]}
    ids = {x["project_id"] for x in body_b["projects"]}
    assert "p" in ids and pid_b in ids
    assert len(names) == 2

    listed = crm.client.get("/api/crm/projects/p/courses").json()
    shared = next(x for x in listed["items"] if x["course_id"] == "olat-shared-1")
    assert shared["project_name"]
    assert {x["project_id"] for x in shared["projects"]} == {"p", pid_b}

    reverse = crm.client.get("/api/crm/courses/olat-shared-1/projects").json()
    assert reverse["course_id"] == "olat-shared-1"
    assert set(reverse["project_names"]) == names
    assert len(reverse["items"]) == 2

    dash = crm.client.get("/api/crm/projects/p/dashboard").json()
    dash_course = next(x for x in dash["courses"] if x["course_id"] == "olat-shared-1")
    assert dash_course["project_name"]
    assert any(x["project_id"] == pid_b for x in dash_course["projects"])


def test_product_course_id_syncs_m2m_and_shows_projects(crm):
    """Product.config.course_id ensures ProjectCourse; product payload lists Project names."""
    pid_b = _second_project(crm, name="Сопровождение")
    created = crm.client.post(
        "/api/crm/projects/p/products",
        json={
            "name": "Курс в каталоге",
            "kind": "course",
            "price_minor": 50000,
            "active": True,
            "config": {"course_id": "olat-from-product"},
        },
    )
    assert created.status_code == 200, created.text
    product = created.json()
    assert product["course_id"] == "olat-from-product"
    assert any(x["project_id"] == "p" for x in product["course_projects"])
    assert product["course_projects"][0]["project_name"]

    with crm.db() as db:
        row = db.scalar(
            select(ProjectCourse).where(
                ProjectCourse.project_id == "p",
                ProjectCourse.course_id == "olat-from-product",
            )
        )
        assert row is not None

    # Same course on second project via explicit bind — product list shows sibling name.
    assert (
        crm.client.post(
            f"/api/crm/projects/{pid_b}/courses",
            json={"course_id": "olat-from-product"},
        ).status_code
        == 200
    )
    products = crm.client.get("/api/crm/projects/p/products").json()
    row = next(x for x in products if x["id"] == product["id"])
    sibling_names = [
        x["project_name"] for x in row["course_projects"] if x["project_id"] == pid_b
    ]
    assert sibling_names
    assert sibling_names[0] == "Сопровождение"


def test_unbind_leaves_other_project_bind(crm):
    pid_b = _second_project(crm, name="Другой")
    crm.client.post("/api/crm/projects/p/courses", json={"course_id": "olat-keep"})
    crm.client.post(f"/api/crm/projects/{pid_b}/courses", json={"course_id": "olat-keep"})
    deleted = crm.client.delete("/api/crm/projects/p/courses/olat-keep")
    assert deleted.status_code == 200, deleted.text
    still = crm.client.get(f"/api/crm/projects/{pid_b}/courses").json()
    assert any(x["course_id"] == "olat-keep" for x in still["items"])
    missing = crm.client.get("/api/crm/courses/missing-course-xyz/projects")
    assert missing.status_code == 404


def test_product_course_change_releases_obsolete_auto_bind(crm):
    """Codex P2: changing/clearing product course_id drops unused auto ProjectCourse."""
    created = crm.client.post(
        "/api/crm/projects/p/products",
        json={
            "name": "Автокурс",
            "kind": "course",
            "price_minor": 1000,
            "active": True,
            "config": {"course_id": "olat-old"},
        },
    ).json()
    assert any(
        x["course_id"] == "olat-old"
        for x in crm.client.get("/api/crm/projects/p/courses").json()["items"]
    )
    updated = crm.client.put(
        f"/api/crm/products/{created['id']}",
        json={
            "name": "Автокурс",
            "kind": "course",
            "price_minor": 1000,
            "active": True,
            "description": "",
            "config": {"course_id": "olat-new"},
        },
    )
    assert updated.status_code == 200, updated.text
    courses = {x["course_id"] for x in crm.client.get("/api/crm/projects/p/courses").json()["items"]}
    assert "olat-new" in courses
    assert "olat-old" not in courses

    cleared = crm.client.put(
        f"/api/crm/products/{created['id']}",
        json={
            "name": "Автокурс",
            "kind": "course",
            "price_minor": 1000,
            "active": True,
            "description": "",
            "config": {},
        },
    )
    assert cleared.status_code == 200, cleared.text
    left = {x["course_id"] for x in crm.client.get("/api/crm/projects/p/courses").json()["items"]}
    assert "olat-new" not in left


def test_product_sync_preserves_manual_label_and_explicit_bind(crm):
    """Codex P2: product sync must not overwrite staff labels; explicit binds survive clear."""
    bind = crm.client.post(
        "/api/crm/projects/p/courses",
        json={"course_id": "olat-manual", "label": "Staff label"},
    ).json()
    assert bind["label"] == "Staff label"
    product = crm.client.post(
        "/api/crm/projects/p/products",
        json={
            "name": "Продукт с тем же курсом",
            "kind": "course",
            "price_minor": 1000,
            "active": True,
            "config": {"course_id": "olat-manual"},
        },
    ).json()
    listed = crm.client.get("/api/crm/projects/p/courses").json()
    row = next(x for x in listed["items"] if x["course_id"] == "olat-manual")
    assert row["label"] == "Staff label"

    crm.client.put(
        f"/api/crm/products/{product['id']}",
        json={
            "name": "Продукт с тем же курсом",
            "kind": "course",
            "price_minor": 1000,
            "active": True,
            "description": "",
            "config": {},
        },
    )
    still = crm.client.get("/api/crm/projects/p/courses").json()
    assert any(x["course_id"] == "olat-manual" and x["label"] == "Staff label" for x in still["items"])


def test_frontend_shows_project_name_on_course_uis():
    panel = (ROOT / "web" / "app" / "crm" / "sections" / "ProjectCoursesPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "data-crm-r11" in panel
    assert "Также в:" in panel
    assert "Проект:" in panel
    ws = (ROOT / "web" / "app" / "crm" / "Workspace.tsx").read_text(encoding="utf-8")
    assert "ProjectCoursesPanel" in ws
    assert "course_projects" in ws
    assert "Также в проектах:" in ws
