import pytest
from conftest import EXAMPLES

from replay_anything import recipe as recipes
from replay_anything.errors import RecipeInvalid, RecipeNotFound
from replay_anything.template import TemplateError, condition, render


def test_examples_are_valid():
    for f in EXAMPLES.glob("*.yaml"):
        r = recipes.load_file(f)
        assert r.host == "127.0.0.1:18765"


def test_design_example_parses():
    r = recipes.load_file(EXAMPLES / "post_chat.yaml")
    assert [s["id"] for s in r.steps] == ["token", "upload", "send"]
    assert r.input_summary() == {"channel": "string", "text": "string", "image": "file?"}


@pytest.mark.parametrize(
    "data, msg",
    [
        ({"name": "x", "description": "d", "site": "ftp://a", "steps": [{"id": "a", "request": "GET /"}]}, "http"),
        ({"name": "x", "description": "d", "site": "https://a", "steps": [{"id": "a"}]}, "exactly one"),
        ({"name": "x", "description": "d", "site": "https://a", "steps": [{"id": "a", "request": "/x"}]}, "METHOD"),
        (
            {
                "name": "x",
                "description": "d",
                "site": "https://a",
                "steps": [{"id": "a", "request": "GET /{{b.id}}"}, {"id": "b", "request": "GET /"}],
            },
            "not defined",
        ),
        (
            {
                "name": "x",
                "description": "d",
                "site": "https://a",
                "steps": [{"id": "a", "request": "GET /"}, {"id": "a", "request": "GET /"}],
            },
            "duplicate",
        ),
        (
            {
                "name": "x",
                "description": "d",
                "site": "https://a",
                "inputs": {"n": {"type": "blob"}},
                "steps": [{"id": "a", "request": "GET /"}],
            },
            "unknown type",
        ),
        ({"name": "x", "site": "https://a", "steps": []}, "missing 'description'"),
    ],
)
def test_invalid_recipes(data, msg):
    with pytest.raises(RecipeInvalid, match=msg):
        recipes.parse(data)


def test_render_keeps_types_and_drops_none():
    scope = {"inputs": {"n": 3, "t": "hi"}, "upload": None}
    assert render("{{inputs.n}}", scope) == 3
    assert render("x {{inputs.n}}", scope) == "x 3"
    assert render(["{{upload.file_id}}"], scope) == []
    with pytest.raises(TemplateError):
        render("{{nope.x}}", scope)


def test_condition():
    scope = {"inputs": {"image": None, "mode": "a"}}
    assert not condition("inputs.image", scope)
    assert condition("not inputs.image", scope)
    assert condition("inputs.mode == a", scope)
    assert condition("{{inputs.mode}} != b", scope)


def test_catalog_find_and_search(home):
    assert recipes.find("post_chat").name == "post_chat"
    assert recipes.find("127.0.0.1:18765/post_chat_ws").name == "post_chat_ws"
    assert [r.name for r in recipes.search(query="websocket")] == ["post_chat_ws"]
    with pytest.raises(RecipeNotFound) as e:
        recipes.find("post")
    assert "127.0.0.1:18765/post_chat" in e.value.candidates


def test_bad_file_does_not_hide_others(home, tmp_path, monkeypatch):
    (home / "recipes").mkdir(parents=True, exist_ok=True)
    (home / "recipes" / "broken.yaml").write_text("name: [", encoding="utf-8")
    found, errors = recipes.catalog()
    assert len(found) == 2 and len(errors) == 1


def _browser_recipe(selector):
    return recipes.parse(
        {
            "name": "t",
            "description": "d",
            "site": "https://x.example",
            "notes": "hand-written",
            "inputs": {"text": "string"},
            "steps": [{"id": "s", "browser": [{"wait_for": {"selector": selector}}]}],
        }
    )


def test_warns_on_input_inside_quoted_selector():
    assert recipes.warnings(_browser_recipe("li:has-text('{{inputs.text}}')"))
    assert recipes.warnings(_browser_recipe('li[title="{{ inputs.text }}"]'))
    assert recipes.warnings(_browser_recipe('li[title="a {{ inputs.text }}"]'))
    assert not recipes.warnings(_browser_recipe("li >> text={{inputs.text}}"))
    assert not recipes.warnings(_browser_recipe("li[data-tid='msg']"))


def test_notes_must_be_text():
    base = {"name": "n", "description": "d", "site": "https://x.example", "steps": [{"id": "s", "request": "GET /"}]}
    assert recipes.parse({**base, "notes": "built from a recording"}).raw["notes"]
    with pytest.raises(RecipeInvalid, match="notes"):
        recipes.parse({**base, "notes": ["x"]})


def test_quoted_input_warning_edge_cases():
    def recipe(action):
        return recipes.parse(
            {
                "name": "n",
                "description": "d",
                "site": "https://x.example",
                "inputs": {"text": "string"},
                "steps": [{"id": "s", "browser": [action]}],
            }
        )

    assert recipes.warnings(recipe({"click": "li:has-text('{{inputs.text}}')"}))
    assert recipes.warnings(recipe({"extract": {"m": "li:has-text('{{inputs.text}}')"}}))
    assert recipes.warnings(recipe({"extract": {"m": {"selector": "li[title='{{inputs.text}}']"}}}))
    assert recipes.warnings(recipe({"wait_for": {"selector": 'li:has-text("it\'s {{inputs.text}}")'}}))
    assert not recipes.warnings(recipe({"click": "li:has-text('a') >> nth={{inputs.text}} >> text='b'"}))
