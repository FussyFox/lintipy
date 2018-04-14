# lintipy

Run python based static file linters on AWS lambda.

## Usage

```python
from lintipy import Handler

def handle(*args, **kwargs):
    return Handler('PEP8', 'pycodestyle', '.')(*args, **kwargs)
```

## See also:

This package is used on [Lambda Lint](https://lambdalint.github.io)
