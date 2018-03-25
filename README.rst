lintipy
=======

**Run python based static file linters on AWS lambda.**

Usage
-----

.. code-block::python

    from lintipy import Handler

    handle = Handler('PEP8', 'pycodestyle', '.')
