# Kornia GHA

## Actions

### Env
This GHA action ([.github/actions/env/action.yml](actions/env/action.yml)) is
responsible for the setup of an environment for kornia in development mode on
CI.

Use the actions:
- `conda-incubator/setup-miniconda`

Has the inputs:
- `python-version`: (string, default: `'3.7'`) the python version desired.
  - The version should be supported by `setup-miniconda` action.
- `pytorch-version`: (string, default: `'1.9.1`') the pytorch version desired.
  - This value will be used to install pytorch using conda from pytorch
    channel.
  - If the value passed is `nightly` the nightly version of pytorch with dynamo
    will be installed from pip.


## Reusable workflows

### Tests
This workflow ([.github/workflows/tests.yml](workflows/tests.yml)) will setup
an environment using the [env](#Env) action, run the tests using pytest and
uploading the coverage report to codecov.

Use the actions:
- `actions/checkout`
- `.github/actions/env`

Has the inputs:
- `os`: (string, default: `ubuntu-latest`) the OS name same as supported by [gha](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions#choosing-github-hosted-runners).
- `python-version`: (json list of strings, default: `'["3.7"]'`) a string with
  format of a json list within strings for each python version desired.
- `pytorch-version`: (json list of strings, default: `'["1.9.1"]'`) a string
  with format of a json list within strings for each pytorch version desired.
- `pytorch-dtype`: (string, default: `float32`) the dtype used to generate
  the tests with pytest.
- `continue-on-error`: (boolean, default: `false`) to set the
  `continue-on-error` behavior on the test step.
- `fail-fast`: (boolean, default: `false`) to set the `fail-fast` behavior on
  the matrix strategy.

A matrix strategy will be adopted from the list of python and pytorch version.
