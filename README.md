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
| django\_maker\_checker/conf.py                                        |       37 |        1 |     97% |        45 |
| django\_maker\_checker/constants.py                                   |       67 |        0 |    100% |           |
| django\_maker\_checker/exceptions.py                                  |        6 |        2 |     67% |     19-20 |
| django\_maker\_checker/management/\_\_init\_\_.py                     |        0 |        0 |    100% |           |
| django\_maker\_checker/management/commands/\_\_init\_\_.py            |        0 |        0 |    100% |           |
| django\_maker\_checker/management/commands/process\_approval\_slas.py |        7 |        0 |    100% |           |
| django\_maker\_checker/model\_change.py                               |       62 |        0 |    100% |           |
| django\_maker\_checker/models.py                                      |      267 |       13 |     95% |54, 73-74, 135, 139, 217, 274, 309, 318, 349, 375, 409, 492 |
| django\_maker\_checker/notify.py                                      |       82 |        2 |     98% |  136, 183 |
| django\_maker\_checker/permissions.py                                 |      105 |        6 |     94% |25, 49, 93, 102, 167, 180 |
| django\_maker\_checker/policies.py                                    |      209 |        8 |     96% |88, 311-313, 321-322, 396-397 |
| django\_maker\_checker/queries.py                                     |      179 |       15 |     92% |73, 94-95, 115, 130, 142, 170, 177, 228, 231, 237-238, 242, 345, 352 |
| django\_maker\_checker/registry.py                                    |       87 |        7 |     92% |75, 99, 110, 131, 137, 182, 192 |
| django\_maker\_checker/services.py                                    |      484 |       13 |     97% |99-101, 217, 287, 300, 399, 419-420, 520, 692, 785, 788 |
| django\_maker\_checker/tasks.py                                       |       10 |        1 |     90% |        21 |
| **TOTAL**                                                             | **1831** |  **122** | **93%** |           |


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