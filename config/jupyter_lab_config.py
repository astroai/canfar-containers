# JupyterLab reads this file for LabApp traits (system /etc/jupyter).
# LabApp.app_name is a class attribute, not a configurable trait — setting it
# via c.LabApp.app_name crashes JupyterLab 4 with "Trait, app_name, not found."
# Browser tab title stays the JupyterLab default; session name is already in the URL.
c = get_config()  # noqa: F821
