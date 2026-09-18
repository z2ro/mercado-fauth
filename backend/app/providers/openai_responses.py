import json
from copy import deepcopy

import httpx


async def structured_output(settings, *, name: str, schema: dict, instructions: str, input_data,
                           transport: httpx.AsyncBaseTransport | None = None) -> tuple[str, dict | None]:
    """Shared OpenAI Responses transport for text and multimodal structured output."""
    key = settings.api_key.get_secret_value()
    if settings.provider != 'openai' or not settings.model or not key:
        raise ValueError('Provider, modelo ou credencial indisponível.')
    async with httpx.AsyncClient(timeout=settings.timeout_seconds, transport=transport) as client:
        response = await client.post('https://api.openai.com/v1/responses', headers={
            'Authorization': f'Bearer {key}',
        }, json={
            'model': settings.model,
            'store': False,
            'instructions': instructions,
            'input': input_data if isinstance(input_data, list) else json.dumps(input_data, ensure_ascii=False),
            'max_output_tokens': 2500,
            'text': {'format': {'type': 'json_schema', 'name': name, 'strict': True,
                                'schema': _strict_schema(schema)}},
        })
        response.raise_for_status()
        envelope = response.json()
    if envelope.get('status') != 'completed':
        raise ValueError('Resposta incompleta.')
    contents = [part for item in envelope.get('output', []) if item.get('type') == 'message'
                for part in item.get('content', [])]
    if len(contents) != 1 or contents[0].get('type') != 'output_text':
        raise ValueError('Resposta recusada ou sem JSON estruturado.')
    usage = envelope.get('usage')
    if not isinstance(usage, dict) or not all(type(usage.get(k)) is int and usage[k] >= 0
                                               for k in ('input_tokens', 'output_tokens')):
        usage = None
    elif type(usage.get('total_tokens')) is not int or usage['total_tokens'] < 0:
        usage = {'input_tokens': usage['input_tokens'], 'output_tokens': usage['output_tokens'],
                 'total_tokens': usage['input_tokens'] + usage['output_tokens']}
    else:
        usage = {key: usage[key] for key in ('input_tokens', 'output_tokens', 'total_tokens')}
    return contents[0]['text'], usage


def _strict_schema(schema: dict) -> dict:
    result = deepcopy(schema)

    def visit(value):
        if isinstance(value, dict):
            if value.get('type') == 'object' and isinstance(value.get('properties'), dict):
                value['additionalProperties'] = False
                value['required'] = list(value['properties'])
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(result)
    return result
