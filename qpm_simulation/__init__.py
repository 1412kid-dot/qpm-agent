"""QPM simulation functions. Importing this package never starts a solver."""
__version__ = "0.2.0"

def describe_conventions(topic='overview',template=None):
    from .conventions import describe_conventions as implementation
    return implementation(topic,template)

def task_schema():
    from .task_schema import task_schema as implementation
    return implementation()

def describe_capabilities():
    from .capabilities import describe_capabilities as implementation
    return implementation()

def validate_task(task):
    from .config import Task
    return Task.from_dict(task) if isinstance(task, dict) else task.validate()

def plan_task(task):
    from .workflow import plan_task as implementation
    return implementation(task)

def run_task(task, output=None, backend=None):
    from .workflow import run_task as implementation
    return implementation(task, output, backend)

def search(task, output=None, backend=None):
    from .workflow import search_task
    return search_task(task, output, backend)

def postprocess(run_dir):
    from .reporting import postprocess as implementation
    return implementation(run_dir)

