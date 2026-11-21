"""Translate provider model metadata and build bounded diagnostic requests."""

from io import BytesIO
import re
from uuid import uuid4
import wave


CAPABILITIES = {
    'reasoning': ('reasoning', 'thinking', 'extended_thinking', 'supports_reasoning', 'supports_thinking'),
    'image_input': ('image_input', 'vision', 'supports_vision', 'supports_image_input'),
    'audio_input': ('audio_input', 'supports_audio_input'),
    'video_input': ('video_input', 'supports_video_input'),
    'tools': ('tools', 'tool_calling', 'function_calling', 'supports_tools'),
    'structured_output': ('structured_output', 'structured_outputs', 'supports_structured_output'),
    'multimodal': ('multimodal', 'supports_multimodal'),
}
TEST_OPERATIONS = ('messages', 'chat/completions', 'responses', 'completions', 'images/generations',
                   'audio/speech', 'embeddings', 'moderations', 'audio/transcriptions',
                   'rerank', 'realtime/client_secrets')


def read_capabilities(metadata):
    nested = metadata.get('capabilities')
    nested = nested if isinstance(nested, dict) else {}
    result = {}
    for name, aliases in CAPABILITIES.items():
        for alias in aliases:
            value = nested.get(alias, metadata.get(alias))
            if isinstance(value, dict):
                value = value.get('supported')
            if type(value) is bool:
                result[name] = value
                break

    architecture = metadata.get('architecture')
    architecture = architecture if isinstance(architecture, dict) else {}
    inputs = metadata.get('input_modalities', architecture.get('input_modalities'))
    outputs = metadata.get('output_modalities', architecture.get('output_modalities'))
    for modalities in (inputs, outputs):
        if isinstance(modalities, list) and len({item.lower() for item in modalities if isinstance(item, str)}) > 1:
            result.setdefault('multimodal', True)
    if isinstance(inputs, list) and inputs and all(isinstance(item, str) for item in inputs):
        inputs = {item.lower() for item in inputs}
        for modality in ('image', 'audio', 'video'):
            result.setdefault(f'{modality}_input', modality in inputs)
        if isinstance(outputs, list) and outputs and all(isinstance(item, str) for item in outputs):
            result.setdefault('multimodal', len(inputs | {item.lower() for item in outputs}) > 1)
    elif isinstance(outputs, list) and outputs and all(isinstance(item, str) for item in outputs):
        # Text-to-image or text-to-audio is not evidence of image or audio input.
        if 'text' in {item.lower() for item in outputs} and any(
                result.get(f'{modality}_input') is True for modality in ('image', 'audio', 'video')):
            result.setdefault('multimodal', True)

    parameters = metadata.get('supported_parameters')
    if isinstance(parameters, list):
        if any(item in parameters for item in ('reasoning', 'reasoning_effort', 'include_reasoning', 'thinking')):
            result.setdefault('reasoning', True)
        if any(item in parameters for item in ('tools', 'tool_choice', 'functions')):
            result.setdefault('tools', True)
        if 'structured_outputs' in parameters:
            result.setdefault('structured_output', True)
    return result


def model_capabilities(model):
    features = read_capabilities(model['metadata'])
    features.update(model.get('capability_overrides', {}))
    if model['kind'] == 'text' and any(features.get(f'{mode}_input') is True for mode in ('image', 'audio', 'video')):
        features.setdefault('multimodal', True)
    return features


def model_probe(protocol, model):
    if protocol == 'anthropic':
        return 'messages'
    if model.get('probe_override'):
        return model['probe_override']
    if model['kind'] == 'image':
        return 'images/generations'
    if model['kind'] == 'audio':
        return 'audio/speech'
    ident = model['id'].lower()
    hints = (
        ('embed', 'embeddings'), ('moderation', 'moderations'), ('whisper', 'audio/transcriptions'),
        ('transcribe', 'audio/transcriptions'), ('rerank', 'rerank'), ('realtime', 'realtime/client_secrets'),
    )
    return next((operation for hint, operation in hints if hint in ident), 'chat/completions')


def transcription_packet(model):
    buffer = BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x00\x00' * 6400)
    boundary = uuid4().hex
    fields = (f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'
              f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="probe.wav"\r\n'
              'Content-Type: audio/wav\r\n\r\n').encode()
    return fields + buffer.getvalue() + f'\r\n--{boundary}--\r\n'.encode(), f'multipart/form-data; boundary={boundary}'


def probe_packet(provider, model, operation):
    ident = model['id']
    prompt = 'Reply with OK.'
    request_options = {'timeout': 30, 'response_meta': True}
    payload = {'model': ident}
    if operation in ('messages', 'chat/completions', 'responses', 'completions'):
        reasoning = model_capabilities(model).get('reasoning') or re.match(r'^(?:o[134]|gpt-[56])(?:[-.]|$)', ident.lower())
        tokens = 256 if reasoning else 32
        if operation == 'responses':
            payload.update(input=prompt, max_output_tokens=tokens)
        elif operation == 'completions':
            payload.update(prompt=prompt, max_tokens=tokens)
        else:
            payload['messages'] = [{'role': 'user', 'content': prompt}]
            token_key = 'max_tokens'
            if operation == 'chat/completions' and (reasoning or provider['base_url'].startswith('https://api.openai.com/')):
                token_key = 'max_completion_tokens'
            payload[token_key] = tokens
    elif operation == 'images/generations':
        payload.update(prompt='A solid white square.', n=1, size='1024x1024')
        if ident.lower().startswith('gpt-image'):
            payload.update(quality='low', output_format='png')
        else:
            payload['response_format'] = 'b64_json'
        request_options.update(timeout=90, limit=12 * 1024 * 1024)
    elif operation == 'audio/speech':
        payload.update(input='テスト。', voice=model.get('test_voice') or 'alloy', response_format='mp3')
        request_options.update(binary=True)
    elif operation in ('embeddings', 'moderations'):
        payload['input'] = 'Test.'
    elif operation == 'audio/transcriptions':
        payload, request_options['content_type'] = transcription_packet(ident)
    elif operation == 'rerank':
        payload.update(query='apple', documents=['apple', 'orange'], top_n=1)
    elif operation == 'realtime/client_secrets':
        payload = {'session': {'type': 'realtime', 'model': ident}}
    return payload, request_options


def probe_summary(operation, result):
    if operation == 'audio/speech':
        if result.startswith(b'ID3') or (len(result) > 2 and result[0] == 255 and result[1] & 224 == 224):
            return f'收到 MP3 · {len(result):,} 字节'
    elif not isinstance(result, dict) or result.get('error'):
        pass
    elif operation == 'images/generations':
        images = result.get('data')
        if isinstance(images, list) and any(isinstance(item, dict) and (item.get('b64_json') or item.get('url')) for item in images):
            return '收到 1 张测试图像的结果'
    elif operation == 'messages':
        blocks = result.get('content')
        if isinstance(blocks, list) and blocks:
            return text_blocks(blocks) or '收到 Messages 响应，未返回可见文本'
    elif operation in ('chat/completions', 'completions'):
        choices = result.get('choices')
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            choice = choices[0]
            message = choice.get('message')
            content = message.get('content') if isinstance(message, dict) else choice.get('text')
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list) and (text := text_blocks(content)):
                return text
            if choice.get('finish_reason') == 'length':
                return '收到模型响应，达到输出上限'
    elif operation == 'responses':
        if result.get('status') in ('completed', 'incomplete') and isinstance(result.get('output'), list):
            blocks = [block for item in result['output'] if isinstance(item, dict)
                      and isinstance(item.get('content'), list) for block in item['content']]
            return text_blocks(blocks) or '收到 Responses 响应，未返回可见文本'
    elif operation == 'audio/transcriptions' and isinstance(result.get('text'), str):
        return result['text'].strip() or '收到转写结果，静音样本无文本'
    elif operation == 'realtime/client_secrets' and isinstance(result.get('value'), str) and result['value']:
        return 'Realtime 会话已获授权，未发送音频'
    elif operation in ('embeddings', 'moderations', 'rerank'):
        field = 'data' if operation == 'embeddings' else 'results'
        if isinstance(result.get(field), list) and result[field]:
            return {'embeddings': '收到向量', 'moderations': '收到审核结果', 'rerank': '收到排序结果'}[operation]
    raise ValueError('接口返回了响应，但未包含此测试所需的模型结果。')


def text_blocks(blocks):
    return ''.join(block['text'] for block in blocks if isinstance(block, dict)
                   and block.get('type') in ('text', 'output_text') and isinstance(block.get('text'), str)).strip()


def probe_usage(result):
    usage = result.get('usage') if isinstance(result, dict) else None
    if not isinstance(usage, dict):
        return {}
    fields = {'input_tokens': ('input_tokens', 'prompt_tokens'), 'output_tokens': ('output_tokens', 'completion_tokens')}
    reported = {}
    for name, keys in fields.items():
        for key in keys:
            if type(usage.get(key)) is int:
                reported[name] = usage[key]
                break
    return reported
