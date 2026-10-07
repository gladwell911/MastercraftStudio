"""Exercise legacy installed skills using isolated project/snapshot directories."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import render_skill as renderer


class LegacyRenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.project = root / "project"
        bmad = self.project / "_bmad"
        bmad.mkdir(parents=True)
        (bmad / "config.toml").write_text(
            '[core]\ncommunication_language="Chinese"\nuser_skill_level="intermediate"\n'
            'document_output_language="Chinese"\n'
            '[modules.bmm]\nimplementation_artifacts="{project-root}/artifacts"\n',
            encoding="utf-8",
        )
        self.skill = root / "legacy-skill"
        self.skill.mkdir()
        (self.skill / "customize.toml").write_text(
            '[workflow]\nactivation_steps_prepend=[]\nimplementation_handoff="Read {spec_file}."\n'
            'on_complete=""\n[[workflow.review_layers]]\nid="review"\nname="Reviewer"\n'
            'instruction="Read {skill-root}/review.md and {diff_file}."\n',
            encoding="utf-8",
        )
        self.sources = {
            "workflow.md": '{{.communication_language}} / {{.user_skill_level}} / '
                           '{{.document_output_language}}\n{{.implementation_artifacts}}\n'
                           '{workflow.activation_steps_prepend}\n{workflow.on_complete}\n'
                           'Read [[bmad-snapshot:step.md]].\n',
            "step.md": '{workflow.implementation_handoff}\n{workflow.review_layers}\n',
            "review.md": "Review {diff_file}.\n",
        }

    def write_sources(self):
        for name, content in self.sources.items():
            (self.skill / name).write_text(content, encoding="utf-8")

    def test_legacy_dispatch_resolves_values_links_and_customization(self):
        self.write_sources()
        before = {p.name: p.read_bytes() for p in self.skill.iterdir()}
        entry = renderer.render(self.project, self.skill)
        text = entry.read_text(encoding="utf-8")
        self.assertIn("Chinese / intermediate / Chinese", text)
        self.assertIn((self.project / "artifacts").as_posix(), text)
        self.assertIn("_None._", text)
        self.assertIn((entry.parent / "step.md").as_posix(), text)
        self.assertNotIn("bmad-snapshot:", text)
        step = (entry.parent / "step.md").read_text(encoding="utf-8")
        self.assertIn("Read {spec_file}.", step)
        self.assertIn("#### Reviewer (`review`)", step)
        self.assertIn((entry.parent / "review.md").as_posix(), step)
        self.assertIn("{diff_file}", step)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.skill.iterdir()})
        self.assertEqual(entry, renderer.render(self.project, self.skill))
        manifest = json.loads((entry.parent / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["inputs"]["resolved_values"]["config.core.communication_language"], "Chinese")

    def test_modern_jinja_and_runtime_placeholders_are_preserved(self):
        self.sources["workflow.md"] = '{{ config.communication_language }}\n{{workflow.on_complete}}\n'
        self.sources["step.md"] = "Read {spec_file} and {project-root}.\n"
        self.write_sources()
        entry = renderer.render(self.project, self.skill)
        self.assertEqual(entry.read_text(encoding="utf-8"), "Chinese\n\n")
        self.assertEqual((entry.parent / "step.md").read_text(encoding="utf-8"), self.sources["step.md"])

    def test_missing_config_still_halts_with_original_line(self):
        self.sources["workflow.md"] = "Header\n{{.missing_config}}\n"
        self.write_sources()
        with self.assertRaisesRegex(renderer.RenderError, r"workflow.md:2: missing config"):
            renderer.render(self.project, self.skill)

    def test_missing_snapshot_and_omitted_source_still_halt(self):
        for target, body in [("missing.md", "content"), ("step.md", " ")]:
            with self.subTest(target=target):
                self.sources["workflow.md"] = "[[bmad-snapshot:" + target + "]]\n"
                self.sources["step.md"] = body
                self.write_sources()
                with self.assertRaisesRegex(renderer.RenderError, "targets (undeclared|omitted) source"):
                    renderer.render(self.project, self.skill)

    def test_resolved_values_are_not_parsed_as_templates(self):
        custom = self.project / "_bmad" / "custom"
        custom.mkdir()
        (custom / "legacy-skill.toml").write_text(
            '[workflow]\nimplementation_handoff="Literal {{.missing_config}} {spec_file}."\n',
            encoding="utf-8",
        )
        self.write_sources()
        entry = renderer.render(self.project, self.skill)
        self.assertIn("Literal {{.missing_config}} {spec_file}.",
                      (entry.parent / "step.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
