lintipy
=======

**Run python based static file linters on AWS lambda.**

Usage
-----

.. code-block:: python

    from lintipy import Handler

    def handle(*args, **kwargs):
        return Handler('PEP8', 'pycodestyle', '.')(*args, **kwargs)
