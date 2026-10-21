"""checks/docx_contrast.py: declared pairings that clear the floor pass; a pairing below it,
or one that cannot be resolved, fails; an unreadable file is a usage error."""
import json

from checks import docx_contrast as gate

MANIFEST = {
    "floor": 4.5,
    "text_pairings": [
        {"label": "body", "fg": "#1A202C", "bg": "#FFFFFF"},
        {"label": "heading band", "fg": "#FFFFFF", "bg": "#1F3A5F"},
    ],
}


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data) if not isinstance(data, str) else data)
    return path


def test_passing_manifest(tmp_path):
    assert gate.main(["x", str(_write(tmp_path, "ok.json", MANIFEST))]) == 0


def test_below_floor_pairing_fails(tmp_path):
    low = json.loads(json.dumps(MANIFEST))
    low["text_pairings"].append({"label": "gold on white", "fg": "#B7791F", "bg": "#FFFFFF"})
    assert gate.main(["x", str(_write(tmp_path, "low.json", low))]) == 1


def test_unresolvable_pairing_fails(tmp_path):
    over_photo = json.loads(json.dumps(MANIFEST))
    over_photo["text_pairings"].append({"label": "caption on photo", "fg": "#FFFFFF",
                                        "bg": "url(photo.jpg)"})
    assert gate.main(["x", str(_write(tmp_path, "photo.json", over_photo))]) == 1


def test_manifest_floor_is_respected(tmp_path):
    relaxed = {"floor": 3.0, "text_pairings": [{"label": "large", "fg": "#B7791F",
                                                  "bg": "#FFFFFF"}]}
    assert gate.main(["x", str(_write(tmp_path, "relaxed.json", relaxed))]) == 0


def test_unparseable_json_exits_2(tmp_path):
    assert gate.main(["x", str(_write(tmp_path, "broken.json", "{ not json"))]) == 2


def test_missing_file_exits_2(tmp_path):
    assert gate.main(["x", str(tmp_path / "absent.json")]) == 2


def test_no_argument_exits_2():
    assert gate.main(["x"]) == 2
