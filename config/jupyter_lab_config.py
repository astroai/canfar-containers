# JupyterLab reads this file for LabApp traits (system /etc/jupyter).
# LabApp.app_name is a class attribute, not a configurable trait — setting it
# via c.LabApp.app_name crashes JupyterLab 4 with "Trait, app_name, not found."
# Browser tab title is set at session start via lab/settings/page_config.json
# (appName = pod hostname); see startup-notebook.sh + session_title.py.
c = get_config()  # noqa: F821
