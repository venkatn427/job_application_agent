"""Promptfoo bridge: exercise production routing with synthetic prompts only."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from pipeline.llm import ModelClient, load_config


def call_api(prompt, options, context):
    load_dotenv()
    options = options.get('config', {})
    config = load_config()
    task = options.get('task', 'fields')
    if options.get('model_alias'):
        config = {**config, 'routes': {**config.get('routes', {}), task: options['model_alias']}}
    response = ModelClient(task, config=config).messages.create(
        system='Answer using only the supplied candidate facts. Never invent qualifications. Return only the answer.',
        messages=[{'role': 'user', 'content': prompt}], max_tokens=512,
    )
    return {'output': response.content[0].text}
