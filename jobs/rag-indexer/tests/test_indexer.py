from __future__ import annotations

import json
import hashlib
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(PACKAGE_ROOT))

from pickcardu_indexer.pipeline import Indexer, normalise_fact, strict_resolution, validate_lane  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


SOURCE_SHA = "c5c2e8be6ad0825a56ded1f1a153ceaf11dc060ab44b120a94406a08207babc2"


def lane(document_id: str, provider: str, *, condition: str = "monthly", value: str = "1", quote: str | None = None) -> dict[str, object]:
    quote = quote or f"카페 {condition} {value}% 할인"
    return {
        "document_id": document_id,
        "provider": provider,
        "source_pdf_sha256": SOURCE_SHA,
        "provenance": {"endpoint": "local-fixture", "model": f"{provider}-fixture", "config_hash": "fixture-v1"},
        "identity": {"issuer_name": "Issuer", "card_name": "Card", "issuer_evidence": {"page": 1, "quote": "Issuer"}, "card_evidence": {"page": 1, "quote": "Card"}},
        "pages": [{"page": 1, "text": f"Issuer Card\n상품 안내: {quote}"}],
        "span_dispositions": [{"page": 1, "quote": "Issuer Card", "kind": "identity"}, {"page": 1, "quote": f"상품 안내: {quote}", "kind": "fact"}],
        "facts": [{"target": "카페", "condition": condition, "value": value, "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": "", "evidence": {"page": 1, "quote": quote}}],
    }


def resolution_payload() -> dict[str, object]:
    return {
        "resolution": {"selected_provider": "luna", "selected_identity_provider": "luna", "reason": "verified", "rejected_relations": []},
        "identity": {
            "issuer_name": "Issuer",
            "card_name": "Card",
            "evidence_refs": {
                provider: {
                    "issuer": {"provider": provider, "page": 1, "quote": "Issuer"},
                    "card": {"provider": provider, "page": 1, "quote": "Card"},
                    "supports_selected": True,
                }
                for provider in ("luna", "upstage")
            },
        },
        "canonical": [{"fact": {"target": "카페", "condition": "monthly", "value": "1", "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": ""}, "evidence_refs": {"luna": {"provider": "luna", "page": 1, "quote": "카페 monthly 1% 할인", "supports_selected": True}, "upstage": {"provider": "upstage", "page": 1, "quote": "카페 monthly 1% 할인", "supports_selected": True}}}],
    }


def add_relation(payload: dict[str, object], *, condition: str, quote: str) -> None:
    payload["pages"][0]["text"] += f"\n{quote}"
    payload["span_dispositions"].append({"page": 1, "quote": quote, "kind": "fact"})
    payload["facts"].append({"target": "카페", "condition": condition, "value": "1", "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": "", "evidence": {"page": 1, "quote": quote}})


def relation_item(condition: str, luna_quote: str, upstage_quote: str, *, luna_supports: bool, upstage_supports: bool) -> dict[str, object]:
    return {
        "fact": {"target": "카페", "condition": condition, "value": "1", "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": ""},
        "evidence_refs": {
            "luna": {"provider": "luna", "page": 1, "quote": luna_quote, "supports_selected": luna_supports},
            "upstage": {"provider": "upstage", "page": 1, "quote": upstage_quote, "supports_selected": upstage_supports},
        },
    }


class IndexerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.pdf"
        self.source.write_bytes(b"%PDF-1.4 fixture")
        self.document_id = "issuer/card"
        self.manifest = self.root / "source-manifest.json"
        write_json(self.manifest, {"documents": [{"document_id": self.document_id, "source_pdf": "source.pdf"}]})
        self.luna_dir, self.upstage_dir = self.root / "luna", self.root / "upstage"
        write_json(self.luna_dir / "issuer__card.json", lane(self.document_id, "luna"))
        write_json(self.upstage_dir / "issuer__card.json", lane(self.document_id, "upstage"))
        self.indexer = Indexer(self.root / "runtime")

    def tearDown(self) -> None:
        self.indexer.close()
        for path in sorted((self.root / "runtime").rglob("*"), reverse=True):
            if path.is_dir():
                path.chmod(0o755)
            else:
                path.chmod(0o644)
        self.temporary.cleanup()

    def execute_indexer(self, *, partial: bool = False) -> dict[str, object]:
        return self.indexer.run(
            self.manifest,
            self.luna_dir,
            self.upstage_dir,
            fake_vectors=True,
            allow_partial=partial,
            config={"strategy": "benefit_hierarchy", "fake_vectors": True, "allow_partial": partial},
        )

    def open_relation_review(self) -> tuple[int, Path]:
        write_json(self.upstage_dir / "issuer__card.json", lane(self.document_id, "upstage", condition="daily", quote="카페 monthly daily 1% 할인"))
        with self.assertRaises(RuntimeError):
            self.execute_indexer()
        review_id = self.indexer.state.status()["reviews"][0]["review_id"]
        after = self.root / "resolution.json"
        payload = resolution_payload()
        payload["canonical"][0]["evidence_refs"]["upstage"] = {"provider": "upstage", "page": 1, "quote": "카페 monthly daily 1% 할인", "supports_selected": False}
        payload["resolution"]["rejected_relations"] = [{"provider": "upstage", "tuple": ["카페", "daily", "1", "%", "", "", "", ""], "reason": "daily differs from selected monthly relation"}]
        write_json(after, payload)
        return review_id, after

    def test_dual_lane_release_resume_and_pointer(self) -> None:
        result = self.execute_indexer()
        release_id = result["release_id"]
        self.assertIsInstance(release_id, str)
        self.assertEqual(result["status"]["run"]["status"], "test_only_published")
        release = self.root / "runtime/index-release" / str(release_id)
        manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["document_ids"], [self.document_id])
        self.assertEqual(manifest["coverage"], {"included_document_ids": [self.document_id], "omitted_document_ids": [], "partial": False})
        connection = sqlite3.connect(release / "corpus.sqlite")
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0], 1)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0], 1)
        connection.close()
        with self.assertRaisesRegex(RuntimeError, "test-only"):
            self.indexer.activate(str(release_id))
        resumed = self.execute_indexer()
        self.assertEqual(resumed["run_id"], result["run_id"])
        self.assertEqual(resumed["release_id"], release_id)

    def test_relation_mismatch_opens_review_and_blocks_publish(self) -> None:
        write_json(self.upstage_dir / "issuer__card.json", lane(self.document_id, "upstage", condition="daily", quote="카페 monthly daily 1% 할인"))
        with self.assertRaisesRegex(RuntimeError, "release blocked"):
            self.execute_indexer()
        status = self.indexer.state.status()
        self.assertEqual(status["documents"][0]["status"], "review")
        self.assertEqual(status["reviews"][0]["kind"], "relation_mismatch")

    def test_resolution_audits_and_revalidates(self) -> None:
        write_json(self.upstage_dir / "issuer__card.json", lane(self.document_id, "upstage", condition="daily", quote="카페 monthly daily 1% 할인"))
        with self.assertRaises(RuntimeError):
            self.execute_indexer()
        review_id = self.indexer.state.status()["reviews"][0]["review_id"]
        after = self.root / "after.json"
        payload = resolution_payload()
        payload["resolution"]["reason"] = "selected grounded Luna relation"
        payload["resolution"]["rejected_relations"] = [{"provider": "upstage", "tuple": ["카페", "daily", "1", "%", "", "", "", ""], "reason": "daily relation differs"}]
        payload["canonical"][0]["evidence_refs"]["upstage"] = {"provider": "upstage", "page": 1, "quote": "카페 monthly daily 1% 할인", "supports_selected": False}
        write_json(after, payload)
        self.indexer.resolve_review(review_id, "reviewer@example.test", "checked source evidence", after, self.luna_dir, self.upstage_dir)
        review = self.indexer.state.review(review_id)
        self.assertEqual(review["status"], "resolved")
        self.assertEqual(review["reviewer"], "reviewer@example.test")
        release_id = self.indexer.publish(review["run_id"], allow_partial=False, fake_vectors=True)
        self.assertTrue((self.root / "runtime/index-release" / release_id / "chroma").is_dir())

    def test_rule_and_grounding_failures_are_not_silenced(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing"):
            normalise_fact({"target": "x", "value": "", "unit": "원"})
        with self.assertRaisesRegex(ValueError, "dash"):
            normalise_fact({"target": "x", "value": "-", "unit": "원"})
        with self.assertRaisesRegex(ValueError, "linked"):
            validate_lane("luna", lane(self.document_id, "luna", value="2", quote="카페 monthly 1% 할인"))
        invalid = lane(self.document_id, "luna")
        invalid["facts"][0]["evidence"]["quote"] = "없는 근거"
        with self.assertRaisesRegex(ValueError, "grounded"):
            validate_lane("luna", invalid)

    def test_field_and_critical_span_coverage_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "condition"):
            validate_lane("luna", lane(self.document_id, "luna", quote="카페 1% 할인"))
        missing = lane(self.document_id, "luna")
        missing["pages"] = [{"page": 1, "text": "Issuer Card\n카페 monthly 1% 할인\n주유 daily 2% 할인"}]
        missing["span_dispositions"] = [{"page": 1, "quote": "Issuer Card", "kind": "identity"}, {"page": 1, "quote": "카페 monthly 1% 할인", "kind": "fact"}]
        with self.assertRaisesRegex(ValueError, "OCR line lacks explicit disposition"):
            validate_lane("luna", missing)
        zero = lane(self.document_id, "luna", value="0", quote="카페 monthly 0% 할인")
        self.assertEqual(len(validate_lane("luna", zero)), 1)

    def test_identity_grounding_and_lane_mismatch_are_reviews(self) -> None:
        ungrounded = lane(self.document_id, "luna")
        ungrounded["identity"]["issuer_evidence"]["quote"] = "Missing issuer"
        with self.assertRaisesRegex(ValueError, "identity is not grounded"):
            validate_lane("luna", ungrounded)
        mismatched = lane(self.document_id, "upstage")
        mismatched["identity"]["card_name"] = "OtherCard"
        mismatched["identity"]["card_evidence"]["quote"] = "Card OtherCard"
        mismatched["pages"][0]["text"] = mismatched["pages"][0]["text"].replace("Issuer Card", "Issuer Card OtherCard")
        mismatched["span_dispositions"][0]["quote"] = "Issuer Card OtherCard"
        write_json(self.upstage_dir / "issuer__card.json", mismatched)
        with self.assertRaises(RuntimeError):
            self.execute_indexer()
        self.assertEqual(self.indexer.state.status()["reviews"][0]["kind"], "identity_mismatch")

    def test_release_identity_mismatch_is_blocked(self) -> None:
        chunks = [{"chunk_id": "a", "document_id": "d", "level": "benefit", "text": "x", "metadata": {}}]
        embeddings = self.indexer._fake_embedding("x", 16).reshape(1, 16)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.indexer._build_fts(root / "corpus.sqlite", chunks)
            self.indexer._build_chroma(root / "chroma", chunks, embeddings)
            with self.assertRaisesRegex(RuntimeError, "manifest identity"):
                self.indexer._verify_release(root, {"chunk_ids": ["wrong"], "document_ids": ["d"]}, chunks, embeddings)

    def test_fts_chroma_identity_mismatch_is_blocked(self) -> None:
        fts_chunks = [{"chunk_id": "a", "document_id": "d", "level": "benefit", "text": "x", "metadata": {}}]
        chroma_chunks = [{"chunk_id": "b", "document_id": "d", "level": "benefit", "text": "x", "metadata": {}}]
        embeddings = self.indexer._fake_embedding("x", 16).reshape(1, 16)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.indexer._build_fts(root / "corpus.sqlite", fts_chunks)
            self.indexer._build_chroma(root / "chroma", chroma_chunks, embeddings)
            with self.assertRaisesRegex(RuntimeError, "FTS5-Chroma identity"):
                self.indexer._verify_release(root, {"chunk_ids": ["a"], "document_ids": ["d"]}, fts_chunks, embeddings)

    def test_provider_artifacts_are_recorded_per_lane(self) -> None:
        self.execute_indexer()
        rows = self.indexer.state.connection.execute(
            "SELECT provider, path FROM artifacts WHERE kind='ocr_json' ORDER BY provider"
        ).fetchall()
        self.assertEqual([(row["provider"], Path(row["path"]).parent.name) for row in rows], [("luna", "luna"), ("upstage", "upstage")])

    def test_changed_lane_input_invalidates_resume(self) -> None:
        first = self.execute_indexer()
        write_json(self.upstage_dir / "issuer__card.json", lane(self.document_id, "upstage", condition="daily"))
        with self.assertRaisesRegex(RuntimeError, "release blocked"):
            self.execute_indexer()
        run_ids = [row[0] for row in self.indexer.state.connection.execute("SELECT run_id FROM runs ORDER BY created_at, run_id")]
        self.assertEqual(len(run_ids), 2)
        self.assertIn(first["run_id"], run_ids)

    def test_resolved_review_retry_is_fully_immutable(self) -> None:
        review_id, after = self.open_relation_review()
        result = self.indexer.resolve_review(review_id, "reviewer", "reason", after, self.luna_dir, self.upstage_dir)
        path = Path(result["canonical_path"])
        before = (sorted(path.parent.iterdir()), hashlib.sha256(path.read_bytes()).hexdigest(), dict(self.indexer.state.review(review_id)))
        with self.assertRaisesRegex(RuntimeError, "not open"):
            self.indexer.resolve_review(review_id, "reviewer", "reason", after, self.luna_dir, self.upstage_dir)
        self.assertEqual(before, (sorted(path.parent.iterdir()), hashlib.sha256(path.read_bytes()).hexdigest(), dict(self.indexer.state.review(review_id))))

    def test_resolve_rejects_stale_source_and_lanes(self) -> None:
        for changed in ("source", "luna", "upstage"):
            with self.subTest(changed=changed):
                self.tearDown(); self.setUp()
                review_id, after = self.open_relation_review()
                if changed == "source":
                    self.source.write_bytes(b"changed")
                else:
                    target = self.luna_dir if changed == "luna" else self.upstage_dir
                    payload = json.loads((target / "issuer__card.json").read_text())
                    payload["provenance"]["config_hash"] = "changed"
                    write_json(target / "issuer__card.json", payload)
                with self.assertRaisesRegex(RuntimeError, "stale"):
                    self.indexer.resolve_review(review_id, "reviewer", "reason", after, self.luna_dir, self.upstage_dir)

    def test_strict_resolution_rejects_mixed_wrong_and_false_support(self) -> None:
        luna, upstage = lane(self.document_id, "luna"), lane(self.document_id, "upstage")
        for kind in ("mixed", "wrong", "false"):
            with self.subTest(kind=kind):
                payload = resolution_payload()
                if kind == "mixed": payload["canonical"][0]["fact"]["condition"] = "daily"
                if kind == "wrong": payload["canonical"][0]["evidence_refs"]["luna"]["quote"] = "wrong"
                if kind == "false": payload["canonical"][0]["evidence_refs"]["upstage"]["supports_selected"] = False
                with self.assertRaises(ValueError): strict_resolution(payload, luna, upstage)

    def test_strict_resolution_requires_complete_selected_and_rejected_sets(self) -> None:
        luna, upstage = lane(self.document_id, "luna"), lane(self.document_id, "upstage")
        add_relation(luna, condition="weekly", quote="카페 weekly 1% 할인")
        add_relation(upstage, condition="daily", quote="카페 daily 1% 할인")
        payload = resolution_payload()
        payload["resolution"]["rejected_relations"] = [{"provider": "upstage", "tuple": ["카페", "daily", "1", "%", "", "", "", ""], "reason": "daily differs"}]
        with self.assertRaisesRegex(ValueError, "exactly equal"):
            strict_resolution(payload, luna, upstage)
        payload["canonical"].append(relation_item("weekly", "카페 weekly 1% 할인", "카페 daily 1% 할인", luna_supports=True, upstage_supports=False))
        canonical, identity, audit = strict_resolution(payload, luna, upstage)
        self.assertEqual([item["fact"]["condition"] for item in canonical], ["monthly", "weekly"])
        self.assertEqual(identity["card_name"], "Card")
        self.assertEqual(audit["resolution"]["rejected_relations"], payload["resolution"]["rejected_relations"])
        payload["resolution"]["rejected_relations"] = []
        with self.assertRaisesRegex(ValueError, "exactly equal"):
            strict_resolution(payload, luna, upstage)

    def test_strict_resolution_accepts_complete_upstage_selection(self) -> None:
        luna, upstage = lane(self.document_id, "luna"), lane(self.document_id, "upstage")
        add_relation(luna, condition="weekly", quote="카페 weekly 1% 할인")
        add_relation(upstage, condition="daily", quote="카페 daily 1% 할인")
        payload = resolution_payload()
        payload["resolution"]["selected_provider"] = "upstage"
        payload["resolution"]["rejected_relations"] = [{"provider": "luna", "tuple": ["카페", "weekly", "1", "%", "", "", "", ""], "reason": "weekly differs"}]
        payload["canonical"].append(relation_item("daily", "카페 weekly 1% 할인", "카페 daily 1% 할인", luna_supports=False, upstage_supports=True))
        canonical, _, _ = strict_resolution(payload, luna, upstage)
        self.assertEqual([item["fact"]["condition"] for item in canonical], ["daily", "monthly"])

    def test_upstage_identity_resolution_preserves_exact_provenance(self) -> None:
        luna, upstage = lane(self.document_id, "luna"), lane(self.document_id, "upstage")
        upstage["identity"]["card_name"] = "OtherCard"
        upstage["identity"]["card_evidence"]["quote"] = "OtherCard"
        upstage["pages"][0]["text"] = upstage["pages"][0]["text"].replace("Issuer Card", "Issuer OtherCard")
        upstage["span_dispositions"][0]["quote"] = "Issuer OtherCard"
        payload = resolution_payload()
        payload["resolution"]["selected_identity_provider"] = "upstage"
        payload["identity"]["card_name"] = "OtherCard"
        payload["identity"]["evidence_refs"]["luna"]["supports_selected"] = False
        payload["identity"]["evidence_refs"]["upstage"]["card"]["quote"] = "OtherCard"
        _, identity, _ = strict_resolution(payload, luna, upstage)
        self.assertEqual(identity["card_name"], "OtherCard")
        self.assertFalse(identity["evidence_refs"]["luna"]["supports_selected"])
        for change in ("wrong_name", "wrong_evidence", "false_support"):
            invalid = json.loads(json.dumps(payload))
            if change == "wrong_name": invalid["identity"]["card_name"] = "Card"
            if change == "wrong_evidence": invalid["identity"]["evidence_refs"]["upstage"]["card"]["quote"] = "wrong"
            if change == "false_support": invalid["identity"]["evidence_refs"]["luna"]["supports_selected"] = True
            with self.subTest(change=change), self.assertRaises(ValueError):
                strict_resolution(invalid, luna, upstage)

    def test_resolve_review_uses_selected_upstage_identity(self) -> None:
        upstage = lane(self.document_id, "upstage")
        upstage["identity"]["card_name"] = "OtherCard"
        upstage["identity"]["card_evidence"]["quote"] = "OtherCard"
        upstage["pages"][0]["text"] = upstage["pages"][0]["text"].replace("Issuer Card", "Issuer OtherCard")
        upstage["span_dispositions"][0]["quote"] = "Issuer OtherCard"
        write_json(self.upstage_dir / "issuer__card.json", upstage)
        with self.assertRaises(RuntimeError):
            self.execute_indexer()
        review_id = self.indexer.state.status()["reviews"][0]["review_id"]
        payload = resolution_payload()
        payload["resolution"]["selected_identity_provider"] = "upstage"
        payload["identity"]["card_name"] = "OtherCard"
        payload["identity"]["evidence_refs"]["luna"]["supports_selected"] = False
        payload["identity"]["evidence_refs"]["upstage"]["card"]["quote"] = "OtherCard"
        after = self.root / "identity-resolution.json"
        write_json(after, payload)
        result = self.indexer.resolve_review(review_id, "reviewer", "Upstage identity is grounded", after, self.luna_dir, self.upstage_dir)
        saved = json.loads(Path(result["canonical_path"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["identity"]["card_name"], "OtherCard")
        self.assertFalse(saved["identity"]["evidence_refs"]["luna"]["supports_selected"])

    def test_canonical_tamper_blocks_publish_and_auto_path_is_hashed(self) -> None:
        result = self.execute_indexer()
        document = result["status"]["documents"][0]
        path = Path(document["canonical_path"])
        self.assertEqual(path.stem, document["canonical_sha256"])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), document["canonical_sha256"])
        path.chmod(0o644); path.write_text("tampered"); path.chmod(0o444)
        with self.assertRaisesRegex(RuntimeError, "canonical artifact hash mismatch"):
            self.indexer.publish(result["run_id"], allow_partial=False, fake_vectors=True)

    def test_resolve_transaction_failure_rolls_back_review_and_document(self) -> None:
        review_id, after = self.open_relation_review()
        self.indexer.state.connection.execute("CREATE TRIGGER reject_canonical BEFORE UPDATE OF canonical_path ON documents BEGIN SELECT RAISE(ABORT, 'trigger'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            self.indexer.resolve_review(review_id, "reviewer", "reason", after, self.luna_dir, self.upstage_dir)
        self.assertEqual(self.indexer.state.review(review_id)["status"], "open")
        self.assertEqual(self.indexer.state.status()["documents"][0]["status"], "review")

    def test_finalized_source_is_read_only_and_serving_is_writable(self) -> None:
        result = self.execute_indexer(); release = self.root / "runtime/index-release" / result["release_id"]
        manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((release / "manifest.json").stat().st_mode & 0o222, 0)
        self.assertEqual((release / "corpus.sqlite").stat().st_mode & 0o222, 0)
        self.assertEqual((release / "chroma").stat().st_mode & 0o222, 0)
        serving = self.root / "runtime/serving" / result["release_id"] / manifest["chroma_tree_sha256"] / "chroma"
        self.assertNotEqual(serving.stat().st_mode & 0o200, 0)
        import chromadb
        collection = chromadb.PersistentClient(path=str(serving)).get_collection("benefit_hierarchy")
        self.assertEqual(len(collection.get(include=[])["ids"]), 1)

    def test_serving_version_is_revalidated_and_never_replaced(self) -> None:
        result = self.execute_indexer()
        release = self.root / "runtime/index-release" / result["release_id"]
        manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
        version = self.root / "runtime/serving" / result["release_id"] / manifest["chroma_tree_sha256"]
        marker = version / "version.json"
        before = (version.stat().st_ino, marker.read_bytes(), hashlib.sha256((version / "chroma/chroma.sqlite3").read_bytes()).hexdigest())
        self.execute_indexer()
        after = (version.stat().st_ino, marker.read_bytes(), hashlib.sha256((version / "chroma/chroma.sqlite3").read_bytes()).hexdigest())
        self.assertEqual(after, before)
        self.assertTrue((self.root / "runtime/serving/.locks" / f"{result['release_id']}.lock").is_file())
        self.assertEqual([path for path in version.parent.iterdir() if path.name.startswith(".")], [])

    def test_serving_version_tamper_is_blocked_without_replacement(self) -> None:
        result = self.execute_indexer()
        release = self.root / "runtime/index-release" / result["release_id"]
        manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
        version = self.root / "runtime/serving" / result["release_id"] / manifest["chroma_tree_sha256"]
        marker = version / "version.json"
        marker_before, inode_before = marker.read_bytes(), version.stat().st_ino
        database = version / "chroma/chroma.sqlite3"
        database.write_bytes(database.read_bytes() + b"tamper")
        with self.assertRaisesRegex(RuntimeError, "content mismatch"):
            self.execute_indexer()
        self.assertEqual((version.stat().st_ino, marker.read_bytes()), (inode_before, marker_before))

    def test_concurrent_serving_materialization_converges_on_one_version(self) -> None:
        result = self.execute_indexer()
        release = self.root / "runtime/index-release" / result["release_id"]
        manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
        version = self.root / "runtime/serving" / result["release_id"] / manifest["chroma_tree_sha256"]
        version.rename(version.with_name("prior-fixture-version"))
        approved = [row for row in self.indexer.state.documents(result["run_id"]) if row["status"] == "canonical_approved"]
        chunks, _ = self.indexer._chunks(result["run_id"], approved)
        embeddings = self.indexer._fake_embedding(chunks[0]["text"], manifest["embedding_dimension"]).reshape(1, -1)
        with ThreadPoolExecutor(max_workers=2) as executor:
            paths = list(executor.map(lambda _: self.indexer._materialize_serving(release, manifest, chunks, embeddings), range(2)))
        self.assertEqual(paths, [version, version])
        self.assertEqual(paths[0].stat().st_ino, paths[1].stat().st_ino)
        self.assertEqual([path for path in version.parent.iterdir() if path.name.startswith(f".{manifest['chroma_tree_sha256']}")], [])

    def test_test_only_run_status(self) -> None:
        self.assertEqual(self.execute_indexer()["status"]["run"]["status"], "test_only_published")

    def test_runtime_has_no_legacy_or_cross_lane_reference(self) -> None:
        source = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE_ROOT.rglob("*.py"))
        self.assertNotIn("note" + "books/", source)
        self.assertNotIn("snap" + "shot", source.casefold())
        self.assertNotIn("ki" + "wi", source.casefold())
        self.assertNotIn("m" + "mr", source.casefold())


if __name__ == "__main__":
    unittest.main()
