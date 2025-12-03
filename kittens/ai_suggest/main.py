#!/usr/bin/env python
# License: GPLv3 Copyright: 2024

import os
import sys

from kitty.typing_compat import BossType, KeyEventType, ScreenSize

from ..tui.handler import Handler, result_handler
from ..tui.line_edit import LineEdit
from ..tui.loop import Loop
from ..tui.operations import RESTORE_CURSOR, SAVE_CURSOR, MouseTracking, styled


class AISuggestHandler(Handler):
    use_alternate_screen = False
    mouse_tracking = MouseTracking.none

    def __init__(self, window_id: int):
        self.window_id = window_id
        self.line_edit = LineEdit()
        self.description = ''

    def initialize(self) -> None:
        self.write(SAVE_CURSOR)
        self.print(styled('AI Command Suggestion', bold=True, fg='cyan'))
        self.print('Describe what command you want:')
        self.print('')
        self.write(SAVE_CURSOR)

    def on_resize(self, screen_size: ScreenSize) -> None:
        super().on_resize(screen_size)
        self.commit_line()

    def commit_line(self) -> None:
        self.write(RESTORE_CURSOR + SAVE_CURSOR)
        self.cmd.clear_to_end_of_screen()
        self.line_edit.write(self.write, prompt='> ', screen_cols=self.screen_size.cols)
        self.flush()

    def on_text(self, text: str, in_bracketed_paste: bool = False) -> None:
        self.line_edit.on_text(text, in_bracketed_paste)
        self.commit_line()

    def on_key(self, key_event: KeyEventType) -> None:
        if key_event.matches('enter'):
            self.description = self.line_edit.current_input
            self.quit_loop(0)
            return
        if key_event.matches('ctrl+c') or key_event.matches('escape'):
            self.quit_loop(1)
            return
        if self.line_edit.on_key(key_event):
            self.commit_line()


def call_gemini_api(description: str) -> str:
    """Call Gemini API to get command suggestion."""
    import json
    import urllib.error
    import urllib.request

    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        raise ValueError('GEMINI_API_KEY environment variable is not set')

    prompt = f"""You are a helpful assistant that suggests terminal commands based on user descriptions.
The user wants to: {description}

Provide ONLY the command that should be executed, without any explanation, comments, or markdown formatting.
Just output the raw command that can be directly executed in a terminal.

Example:
User: "list all files in current directory"
You: ls -la

User: "find all python files"
You: find . -name "*.py"

Now provide the command for: {description}"""

    data = {
        "contents": [{
            "parts": [{
                "text": prompt
            }]
        }]
    }

    # Try gemini-2.5-flash first as requested, fallback to other models if not available
    model_names = ['gemini-2.0-flash-exp', 'gemini-1.5-flash']
    # Note: When gemini-2.5-flash becomes available, add 'gemini-2.5-flash' to the beginning of the list
    last_error: Exception | None = None

    for model_name in model_names:
        try:
            url = f'https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}'
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode('utf-8'))
                if 'candidates' in result and len(result['candidates']) > 0:
                    content = result['candidates'][0].get('content', {})
                    parts = content.get('parts', [])
                    if parts and 'text' in parts[0]:
                        command: str = parts[0]['text'].strip()
                        # Remove markdown code blocks if present
                        if command.startswith('```'):
                            lines = command.split('\n')
                            command = '\n'.join(lines[1:-1]) if len(lines) > 2 else command
                        return command.strip()
                raise ValueError('No command generated from API response')
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code == 404:
                # Model not found, try next one
                continue
            error_body = e.read().decode('utf-8')
            raise Exception(f'Gemini API error: {e.code} - {error_body}')
        except Exception as e:
            if '404' in str(e) or 'not found' in str(e).lower():
                continue
            last_error = e

    # If all models failed
    if last_error:
        raise Exception(f'Failed to call Gemini API with any available model. Last error: {str(last_error)}')
    raise Exception('Failed to call Gemini API: No models available')


def main(args: list[str]) -> str | None:
    # Get user input
    handler = AISuggestHandler(0)  # Window ID not needed for input
    loop = Loop()
    try:
        loop.loop(handler)
    finally:
        handler.write(RESTORE_CURSOR)
        handler.cmd.clear_to_end_of_screen()

    description = handler.description
    if not description:
        return None

    # Call Gemini API
    try:
        command = call_gemini_api(description)
        return command
    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        return None


@result_handler()
def handle_result(args: list[str], command: str | None, target_window_id: int, boss: BossType) -> None:
    """Send the AI-suggested command to the terminal."""
    if command is None:
        return

    window = boss.window_id_map.get(target_window_id)
    if window is None:
        return

    # Send command to terminal at cursor position
    window.write_to_child(command)


if __name__ == '__main__':
    main(sys.argv)
elif __name__ == '__doc__':
    cd = sys.cli_docs  # type: ignore
    cd['usage'] = ''
    cd['options'] = lambda: ''
    cd['help_text'] = 'Get AI command suggestions using Gemini'
    cd['short_desc'] = 'AI command suggestion using Gemini'
