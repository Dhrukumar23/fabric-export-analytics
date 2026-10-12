"""Deploys Lakehouse, notebook, semantic model and report to a Microsoft Fabric workspace.

Uses Microsoft's fabric-cicd library and a service principal. In GitHub Actions the
deploy job runs only when these repository secrets exist:
  FABRIC_WORKSPACE_ID, AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET

Local use:  pip install -e .[deploy]  then  python fabric/deploy.py
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    from azure.identity import ClientSecretCredential
    from fabric_cicd import FabricWorkspace, publish_all_items

    cred = ClientSecretCredential(os.environ["AZURE_TENANT_ID"], os.environ["AZURE_CLIENT_ID"],
                                  os.environ["AZURE_CLIENT_SECRET"])
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        for item in (ROOT / "fabric" / "workspace").iterdir():
            shutil.copytree(item, stage / item.name)
        for item in (ROOT / "powerbi").glob("*.SemanticModel"):
            shutil.copytree(item, stage / item.name, ignore=shutil.ignore_patterns(".pbi"))
        for item in (ROOT / "powerbi").glob("*.Report"):
            shutil.copytree(item, stage / item.name, ignore=shutil.ignore_patterns(".pbi"))
        ws = FabricWorkspace(
            workspace_id=os.environ["FABRIC_WORKSPACE_ID"],
            repository_directory=str(stage),
            item_type_in_scope=["Lakehouse", "Notebook", "SemanticModel", "Report"],
            token_credential=cred,
        )
        publish_all_items(ws)
    print("Deployed to Fabric workspace", os.environ["FABRIC_WORKSPACE_ID"])


if __name__ == "__main__":
    main()
