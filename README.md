# 4W Scoring Agent
Private Windows evaluation of a native Codex specialist with local workbook helpers. Synthetic examples only.

## Install
1. Clone or download the approved private release, open its folder as a Codex project, and accept the folder trust prompt. Use the local environment rooted at that folder. A CLI started with environments=[] has no project workspace and cannot validate this agent. For the desktop-supplied CLI, use --no-daemon if background-server startup reports an incomplete local package.
2. Have your own authorized Codex account, gpt-6-astra access, Python 3.13, Node.js 22+ and a compatible Codex-provided @oai/artifact-tool runtime.
3. In PowerShell run:
   powershell -NoProfile -File .\scripts\install.ps1
   If detection fails, supply -ArtifactRuntime "C:\path\to\node\node_modules" and -Node "C:\path\to\node.exe". The directory must contain @oai/artifact-tool. The installer never downloads it.
4. Run .\scripts\verify.ps1 -Tests for the synthetic regression checks. Native invocation is verified separately in Codex.

## Paste into this project chat
Use the 4W Scoring Agent for the artificial workbook runs/demo/source.xlsx. This is a synthetic-only evaluation. Read AGENTS.md and docs/CONTRACTS.md. Run prepare, supply each complete frozen request JSON sequentially to the named specialist, preserve its exact first final response, then accept and finish. Use at most 16 cases per specialist request. Verify the final receipt and give me the link to runs/demo/scored/results.xlsx. Report counts and status only. Keep original workbook bytes unchanged.

Use only the package-generated artificial workbook. Each run writes a separate derived result. The package rejects real-case requests. Runtime tool/memory isolation and clinical accuracy are unverified.

## Results and updates
The scored copy is runs/demo/scored/results.xlsx after verified completion. Never treat the installer transport workbook as scored results.
Use the release VERSION and release-manifest.json to identify the package. Download updates into a separate empty folder and rerun installation. Run .\scripts\uninstall.ps1 to remove the package-created Python environment, subject to identity checks. Preserve any runs before removing a downloaded folder.
See docs/RECOVERY.md and docs/LIMITATIONS.md. No API key, shared credentials, production scoring or Mac acceptance is included.
