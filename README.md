# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/joemash/django-maker-checker/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                                  |    Stmts |     Miss |   Cover |   Missing |
|---------------------------------------------------------------------- | -------: | -------: | ------: | --------: |
| django\_maker\_checker/\_\_init\_\_.py                                |        1 |        0 |    100% |           |
| django\_maker\_checker/actions.py                                     |       71 |        2 |     97% |  138, 151 |
| django\_maker\_checker/adapter.py                                     |       42 |        1 |     98% |        94 |
| django\_maker\_checker/admin.py                                       |       47 |       47 |      0% |      8-86 |
| django\_maker\_checker/apps.py                                        |       13 |        0 |    100% |           |
| django\_maker\_checker/audit.py                                       |        8 |        2 |     75% |     16-17 |
| django\_maker\_checker/auth\_adapter.py                               |       47 |        2 |     96% |    58, 73 |
| django\_maker\_checker/conf.py                                        |       39 |        1 |     97% |        46 |
| django\_maker\_checker/constants.py                                   |       68 |        0 |    100% |           |
| django\_maker\_checker/exceptions.py                                  |        6 |        2 |     67% |     19-20 |
| django\_maker\_checker/management/\_\_init\_\_.py                     |        0 |        0 |    100% |           |
| django\_maker\_checker/management/commands/\_\_init\_\_.py            |        0 |        0 |    100% |           |
| django\_maker\_checker/management/commands/process\_approval\_slas.py |        7 |        0 |    100% |           |
| django\_maker\_checker/model\_change.py                               |       62 |        0 |    100% |           |
| django\_maker\_checker/models.py                                      |      270 |       13 |     95% |66, 85-86, 147, 151, 229, 286, 321, 330, 361, 387, 421, 504 |
| django\_maker\_checker/notify.py                                      |       84 |        2 |     98% |  150, 197 |
| django\_maker\_checker/permissions.py                                 |      106 |        6 |     94% |25, 49, 94, 103, 168, 181 |
| django\_maker\_checker/policies.py                                    |      214 |        8 |     96% |88, 321-323, 331-332, 406-407 |
| django\_maker\_checker/queries.py                                     |      179 |       15 |     92% |73, 94-95, 115, 130, 142, 170, 177, 228, 231, 237-238, 242, 347, 354 |
| django\_maker\_checker/registry.py                                    |       87 |        7 |     92% |75, 99, 110, 131, 137, 182, 192 |
| django\_maker\_checker/services.py                                    |      550 |       15 |     97% |100-102, 228, 299, 314, 414, 443-444, 544, 742, 791, 816, 913, 916 |
| django\_maker\_checker/tasks.py                                       |       10 |        1 |     90% |        21 |
| **TOTAL**                                                             | **1911** |  **124** | **94%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/joemash/django-maker-checker/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/joemash/django-maker-checker/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/joemash/django-maker-checker/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/joemash/django-maker-checker/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Fjoemash%2Fdjango-maker-checker%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/joemash/django-maker-checker/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.