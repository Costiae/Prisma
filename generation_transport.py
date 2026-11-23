"""Bounded chat response decoding and diagnostics for OpenAI and Anthropic."""
import json
import re

from provider_model_info import probe_usage


class VisibleText:
    """Keep inline <think> blocks out of previews, even across transport chunks."""

    def __init__(self):
        self.parts = []
        self.reasoning = 0
        self.length = 0
        self.tail = ''
        self.pending = ''
        self.thinking = False
        self.answer_started = False

    def _append(self, text):
        if self.thinking:
            self.reasoning += len(text)
        elif text:
            self.parts.append(text)
            self.length += len(text)
            self.tail = (self.tail + text)[-1200:]
            self.answer_started = self.answer_started or bool(text.strip())

    def feed(self, text, *, final=False):
        text = self.pending + text
        self.pending = ''
        if self.answer_started:
            self._append(text)
            return
        end = 0
        for match in re.finditer(r'</?think>', text, re.IGNORECASE):
            self._append(text[end:match.start()])
            if self.answer_started:
                self._append(text[match.start():])
                return
            self.thinking = not match[0].startswith('</')
            end = match.end()
        text = text[end:]
        if not final:
            tags = ('<think>', '</think>')
            partial = max((n for n in range(1, min(len(text), 7) + 1)
                           if any(tag.startswith(text[-n:].lower()) for tag in tags)), default=0)
            if partial:
                self.pending = text[-partial:]
                text = text[:-partial]
        self._append(text)

    @property
    def body(self):
        return ''.join(self.parts)

    def stats(self):
        return {'content_chars': self.length, 'reasoning_chars': self.reasoning, 'preview': self.tail}


def token_usage(value):
    counts = probe_usage(value)
    raw = value.get('usage') if isinstance(value, dict) else None
    if not isinstance(raw, dict):
        return counts
    details = raw.get('completion_tokens_details')
    prompt = raw.get('prompt_tokens_details')
    cached = prompt.get('cached_tokens') if isinstance(prompt, dict) else None
    fields = {'total_tokens': raw.get('total_tokens'),
              'cache_read_input_tokens': raw.get('cache_read_input_tokens', cached),
              'cache_creation_input_tokens': raw.get('cache_creation_input_tokens'),
              'reasoning_tokens': raw.get('reasoning_tokens', details.get('reasoning_tokens') if isinstance(details, dict) else None)}
    counts.update({key: count for key, count in fields.items() if type(count) is int})
    return counts


def _openai_message(value):
    choices = value.get('choices')
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get('message') or choice.get('delta')
    return choice, message if isinstance(message, dict) else {}


def _openai_text(message, output):
    for key in ('reasoning_content', 'reasoning', 'thinking'):
        if isinstance(value := message.get(key), str):
            output.reasoning += len(value)
            break
    if isinstance(content := message.get('content'), str):
        output.feed(content)


def chat_body(value, protocol):
    """Return visible text even from an invalid or truncated model answer."""
    if not isinstance(value, dict):
        return ''
    if protocol == 'anthropic':
        blocks = value.get('content')
        if not isinstance(blocks, list):
            return ''
        return ''.join(item['text'] for item in blocks
                       if isinstance(item, dict) and item.get('type') == 'text' and isinstance(item.get('text'), str))
    content = _openai_message(value)[1].get('content')
    return content if isinstance(content, str) else ''


def buffered_chat(value, protocol, progress):
    if not isinstance(value, dict):
        return value
    output = VisibleText()
    if protocol == 'anthropic':
        finish = value.get('stop_reason')
        blocks = value.get('content')
        if isinstance(blocks, list):
            for block in blocks:
                if isinstance(block, dict) and block.get('type') == 'thinking' and isinstance(text := block.get('thinking'), str):
                    output.reasoning += len(text)
    else:
        choice, message = _openai_message(value)
        finish = choice.get('finish_reason')
        _openai_text(message, output)
    output.feed(chat_body(value, protocol) if protocol == 'anthropic' else '', final=True)
    if protocol == 'anthropic' and isinstance(value.get('content'), list):
        value['content'] = [{'type': 'text', 'text': output.body}]
    elif protocol != 'anthropic' and isinstance(message.get('content'), str):
        message['content'] = output.body
    progress('received', **output.stats(), usage=token_usage(value), finish_reason=finish)
    return value


def event_frames(response, limit):
    size, lines = 0, []
    while raw := response.readline(limit + 1):
        size += len(raw)
        if size > limit:
            raise ValueError('API 响应超过大小限制。')
        if not raw.strip():
            if lines:
                yield b'\n'.join(lines)
                lines.clear()
        elif raw.startswith(b'data:'):
            lines.append(raw[5:].strip())
    if lines:
        yield b'\n'.join(lines)


def streamed_chat(response, protocol, limit, progress):
    output = VisibleText()
    usage, finish, done = {}, None, False
    for frame in event_frames(response, limit):
        if frame == b'[DONE]':
            done = True
            break
        item = json.loads(frame)
        if not isinstance(item, dict):
            raise ValueError('流式响应的数据帧不是 JSON 对象。')
        if item.get('error') or item.get('type') == 'error':
            error = item.get('error')
            message = error.get('message') if isinstance(error, dict) else None
            raise ValueError('模型流式请求失败。' + (f' {message}' if isinstance(message, str) else ''))
        if isinstance(item.get('usage'), dict):
            usage.update(item['usage'])
        if protocol == 'anthropic':
            kind = item.get('type')
            if kind == 'message_start':
                usage.update((item.get('message') or {}).get('usage') or {})
            elif kind == 'message_delta':
                finish = (item.get('delta') or {}).get('stop_reason') or finish
            elif kind == 'message_stop':
                done = True
                break
            else:
                block = item.get('delta') or item.get('content_block') or {}
                if isinstance(text := block.get('text'), str):
                    output.feed(text)
                if isinstance(thinking := block.get('thinking'), str):
                    output.reasoning += len(thinking)
        else:
            choice, message = _openai_message(item)
            finish = choice.get('finish_reason') or finish
            _openai_text(message, output)
        progress('stream', **output.stats())
    output.feed('', final=True)
    if protocol == 'anthropic':
        value = {'content': [{'type': 'text', 'text': output.body}], 'stop_reason': finish, 'usage': usage}
    else:
        value = {'choices': [{'message': {'content': output.body}, 'finish_reason': finish}], 'usage': usage}
    progress('received', **output.stats(), usage=token_usage(value), finish_reason=finish)
    if not (done or finish):
        raise ValueError('模型流式连接提前关闭，未收到结束标记。')
    return value
