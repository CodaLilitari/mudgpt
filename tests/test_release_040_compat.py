"""Synthetic 0.4 release metadata and offline declared-version contracts.

No game, network, provider credentials or production runtime is used. The exact
frozen 0.3 updater is exercised separately by the release coordinator outside
the checkout; the legacy fixture below documents its small asset-ID contract.
"""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("updater_040_contract", ROOT / "mudweave_update.py")
U = importlib.util.module_from_spec(spec)
spec.loader.exec_module(U)
sys.path.insert(0, str(ROOT))
import mudweave

IDS = ("codex", "claude", "muse", "lily")
NOW = 1795132800.0
SETTINGS = {"schema_version": 1, "project_id": 87773838,
            "iteration": "lily", "collection_version": "0.4.0"}


def components(core="0.3.0", adapter="0.1.0"):
    return {
        "core": {"version": core, "status": "included" if core else "not_applicable"},
        "adapter": {"version": adapter, "status": "included" if adapter else "not_applicable"},
        "updater": {"version": "0.4.0", "status": "included"},
    }


def runtime(iteration="lily"):
    result = {"schema_version": 1, "collection_version": "0.4.0",
              "iteration": iteration, "components": components()}
    if iteration == "collection":
        result["components"] = components(None, None)
        result["iterations"] = [
            {"iteration": name, "components": components("0.1.0" if name == "claude" else "0.3.0")}
            for name in IDS
        ]
    return result


def release_fixture():
    assets = [
        {"iteration": name, "name": f"mudweave-{name}-0.4.0.zip", "version": "0.4.0",
         "size_bytes": 16, "sha256": "c" * 64}
        for name in IDS
    ]
    manifest = {"collection_version": "0.4.0", "assets": assets[:3],
                "additional_assets": assets[3:]}
    release = {"tag_name": "v0.4.0", "released_at": U._iso(NOW - 60),
               "upcoming_release": False, "commit": {"id": "b" * 40},
               "assets": {"links": [
                   {"name": row["name"], "url":
                    "https://gitlab.com/api/v4/projects/87773838/packages/generic/"
                    f"mudgpt-clients/v0.4.0/{row['name']}"}
                   for row in assets
               ]}}
    return release, manifest


class ReleaseCompatibility(unittest.TestCase):
    def test_legacy_assets_contract_keeps_new_id_outside_old_list(self):
        _, manifest = release_fixture()
        # Exact 0.3 allowed-ID/row iteration: every assets row is validated,
        # while unknown top-level keys are ignored. Do not put Lily in assets.
        old_ids = {"codex", "claude", "muse", "collection"}
        self.assertTrue(all(row["iteration"] in old_ids for row in manifest["assets"]))
        self.assertEqual([row["iteration"] for row in manifest["additional_assets"]], ["lily"])

    def test_all_four_iteration_settings_and_release_selection(self):
        release, manifest = release_fixture()
        for name in IDS:
            with self.subTest(iteration=name), patch.object(U, "_fetch_json", side_effect=[release, manifest]):
                settings = U.load_settings(dict(SETTINGS, iteration=name))
                result = U._release(settings, NOW)
                self.assertEqual(result["asset"]["iteration"], name)
                self.assertEqual(result["latest"], "0.4.0")

    def test_existing_three_asset_release_needs_no_extension(self):
        release, manifest = release_fixture()
        manifest.pop("additional_assets")
        with patch.object(U, "_fetch_json", side_effect=[release, manifest]):
            self.assertEqual(U._release(dict(SETTINGS, iteration="codex"), NOW)["asset"]["iteration"], "codex")

    def test_duplicates_across_asset_lists_are_rejected(self):
        release, manifest = release_fixture()
        manifest["additional_assets"].append(copy.deepcopy(manifest["assets"][0]))
        with patch.object(U, "_fetch_json", side_effect=[release, manifest]), self.assertRaises(U.UpdateError):
            U._release(dict(SETTINGS, iteration="codex"), NOW)

    def test_malformed_additional_assets_are_rejected_for_every_client(self):
        for extension in ({"lily": {}}, [None], [{"iteration": "unrecognized"}], [0] * 17):
            with self.subTest(extension=extension):
                release, manifest = release_fixture()
                manifest["additional_assets"] = extension
                with patch.object(U, "_fetch_json", side_effect=[release, manifest]), self.assertRaises(U.UpdateError):
                    U._release(dict(SETTINGS, iteration="codex"), NOW)

    def test_lily_generic_asset_path_is_allowed_without_foreign_origin(self):
        name = "mudweave-lily-0.1.0.zip"
        url = f"https://gitlab.com/api/v4/projects/87773838/packages/generic/mudweave-lily/v0.4.0/{name}"
        self.assertEqual(U._asset_url(url, name), url)
        with self.assertRaises(U.UpdateError):
            U._asset_url(url.replace("gitlab.com", "example.invalid"), name)


class DeclaredVersions(unittest.TestCase):
    def test_components_remain_distinct_from_collection_and_updater(self):
        manifest = runtime()
        with patch.object(U, "_fetch_json") as network, patch.object(U, "_cache_root") as cache:
            result = U.versions(SETTINGS, manifest=manifest)
        self.assertEqual(result["status"], "declared_versions")
        self.assertEqual(result["collection_version"], "0.4.0")
        self.assertEqual(result["components"]["core"]["version"], "0.3.0")
        self.assertEqual(result["components"]["adapter"]["version"], "0.1.0")
        self.assertEqual(result["components"]["updater"]["version"], "0.4.0")
        self.assertEqual(result["evidence"], "public_runtime_manifest")
        self.assertIs(result["runtime_verified"], False)
        network.assert_not_called()
        cache.assert_not_called()

    def test_collection_can_report_four_separate_components(self):
        result = U.versions(dict(SETTINGS, iteration="collection"), manifest=runtime("collection"))
        self.assertEqual([row["iteration"] for row in result["iterations"]], list(IDS))
        self.assertIsNone(result["components"]["core"]["version"])
        self.assertEqual(result["iterations"][1]["components"]["core"]["version"], "0.1.0")

    def test_missing_manifest_does_not_promote_update_support_to_runtime(self):
        with tempfile.TemporaryDirectory(prefix="mudweave-versions-") as folder, patch.object(U, "INSTALL_DIR", Path(folder)):
            result = U.versions(dict(SETTINGS, iteration="codex", collection_version="0.3.0"))
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "runtime_manifest_missing")
        self.assertEqual(result["components"], {})
        self.assertIs(result["runtime_verified"], False)

    def test_mismatched_invalid_or_extra_manifest_fields_are_not_echoed(self):
        examples = []
        wrong = runtime(); wrong["iteration"] = "muse"; examples.append(wrong)
        wrong = runtime(); wrong["collection_version"] = "0.3.0"; examples.append(wrong)
        wrong = runtime(); wrong["components"]["adapter"]["version"] = "secret-example"; examples.append(wrong)
        wrong = runtime(); wrong["components"]["core"]["status"] = "actually_running"; examples.append(wrong)
        for manifest in examples:
            with self.subTest(manifest=manifest):
                result = U.versions(SETTINGS, manifest=manifest)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["components"], {})
                self.assertNotIn("secret-example", json.dumps(result))
        valid = runtime(); valid["secret-example"] = "private-extra-not-for-display"
        result = U.versions(SETTINGS, manifest=valid)
        self.assertNotIn("private-extra", json.dumps(result))

    def test_status_adds_local_version_report_without_network(self):
        with patch.object(U, "_load_runtime_manifest", return_value=runtime()), \
             patch.object(U, "_cache_root", return_value=Path("synthetic-cache")), \
             patch.object(U, "_enabled", return_value=True), \
             patch.object(U, "_read", return_value={}), patch.object(U, "_fetch_json") as network:
            result = U.status(SETTINGS)
        self.assertEqual(result["status"], "not_checked")
        self.assertEqual(result["version_report"]["components"]["adapter"]["version"], "0.1.0")
        network.assert_not_called()

    def test_versions_cli_is_offline_json_with_lily_id(self):
        with patch.object(U, "_load_runtime_manifest", return_value=runtime()), \
             patch.object(U, "_fetch_json") as network, contextlib.redirect_stdout(io.StringIO()) as output:
            code = U.main(SETTINGS, ["versions", "--iteration", "lily", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["iteration"], "lily")
        network.assert_not_called()

    def test_human_versions_output_names_collection_core_adapter_and_updater(self):
        with patch.object(U, "_load_runtime_manifest", return_value=runtime()), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(U.main(SETTINGS, ["versions"]), 0)
        for label in ("collection", "core", "adapter", "updater", "not verified"):
            self.assertIn(label, output.getvalue().lower())

    def test_launcher_routes_versions_without_starting_runtime_or_check(self):
        with patch.object(mudweave.updates, "main", return_value=0) as dispatch, \
             patch.object(mudweave.updates, "start_check") as check, \
             patch.object(mudweave.subprocess, "Popen") as spawn:
            self.assertEqual(mudweave.main(["versions", "--json"]), 0)
        dispatch.assert_called_once_with(argv=["versions", "--json"])
        check.assert_not_called()
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
