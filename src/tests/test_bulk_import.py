from __future__ import annotations

import unittest

from app.use_cases import extract_text_import_prompts


class BulkPromptImportParsingTests(unittest.TestCase):
    def test_extracts_one_prompt_per_text_fence_and_ignores_outer_text(self) -> None:
        input_text = """intro

```text
/goal first task
line two
```

ignored

```text
/goal second task
```

```text

/goal third task

```
"""

        self.assertEqual(
            (
                "/goal first task\nline two",
                "/goal second task",
                "/goal third task",
            ),
            extract_text_import_prompts(input_text),
        )

    def test_rejects_input_without_text_fence(self) -> None:
        with self.assertRaisesRegex(ValueError, "```text"):
            extract_text_import_prompts("/goal only outer text")

    def test_rejects_unclosed_text_fence(self) -> None:
        with self.assertRaisesRegex(ValueError, "닫히지 않은"):
            extract_text_import_prompts("```text\n/goal missing close")

    def test_empty_text_fences_do_not_register_prompts(self) -> None:
        with self.assertRaisesRegex(ValueError, "```text"):
            extract_text_import_prompts("```text\n\n```\n")


if __name__ == "__main__":
    unittest.main()
