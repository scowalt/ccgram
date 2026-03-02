"""Tests for interactive prompt text formatting."""

from ccbot.interactive_prompt_formatter import format_codex_interactive_prompt


class TestFormatCodexInteractivePrompt:
    def test_edit_prompt_compacts_diff_and_splits_options(self) -> None:
        raw = (
            "Do you want to make this edit to src/ccbot/bot.py?\n"
            "947    936 -    await register_commands(application.bot, provider=get_provider())"
            "    948 +    await register_commands(application.bot, providers=_menu_providers())\n"
            "953          try:\n"
            "942 -            await register_commands(context.bot, provider=get_provider())"
            "    954 +            await register_commands(context.bot, providers=_menu_providers())\n"
            "› 1. Yes, proceed (y)  2. Yes, and don't ask again for these files (a)"
            "  3. No, and tell Codex what to do differently (esc)\n"
            "Press enter to confirm or esc to cancel\n"
        )

        result = format_codex_interactive_prompt(raw, "SelectionUI")

        assert "Do you want to make this edit to src/ccbot/bot.py?" in result
        assert "File: src/ccbot/bot.py" in result
        assert "Changes: +" in result
        assert "Preview:" in result
        assert "› 1. Yes, proceed (y)" in result
        assert "  2. Yes, and don't ask again for these files (a)" in result
        assert "  3. No, and tell Codex what to do differently (esc)" in result
        assert "Press enter to confirm or esc to cancel" in result

    def test_non_edit_prompt_only_normalizes_options(self) -> None:
        raw = "Which option should I use?\n› 1. A  2. B  3. C\nEsc to cancel\n"

        result = format_codex_interactive_prompt(raw, "SelectionUI")

        assert "Which option should I use?" in result
        assert "Changes:" not in result
        assert "Preview:" not in result
        assert "› 1. A" in result
        assert "  2. B" in result
        assert "  3. C" in result
        assert "Esc to cancel" in result

    def test_idempotent_for_already_formatted_edit_prompt(self) -> None:
        raw = (
            "Do you want to make this edit to src/ccbot/bot.py?\n"
            "File: src/ccbot/bot.py\n"
            "Changes: +2 -2\n"
            "Preview:\n"
            "  - old line\n"
            "  + new line\n"
            "\n"
            "› 1. Yes, proceed (y)\n"
            "  2. Yes, and don't ask again for these files (a)\n"
            "  3. No, and tell Codex what to do differently (esc)\n"
            "Press enter to confirm or esc to cancel\n"
        )

        once = format_codex_interactive_prompt(raw, "SelectionUI")
        twice = format_codex_interactive_prompt(once, "SelectionUI")

        assert once == twice
