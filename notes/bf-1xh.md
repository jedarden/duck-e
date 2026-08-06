# bf-1xh: Add auto-deploy step to duck-e-build

## Summary

Added an `update-declarative-config` step to the duck-e-build WorkflowTemplate in `k8s/iad-ci/argo-workflows/duck-e-workflowtemplate.yml` (in declarative-config repo). This step runs after the Docker image build succeeds and ensures that the deployment manifest on ardenone-cluster gets updated with the new image tag.

## What Changed

**File:** jedarden/declarative-config/k8s/iad-ci/argo-workflows/duck-e-workflowtemplate.yml

1. **Added step in build template** (after docker-build):
   - Calls `update-declarative-config` template with the resolved version

2. **Added new update-declarative-config template**:
   - Clones jedarden/declarative-config
   - Finds all `.yaml` and `.yml` files containing `ronaldraygun/duck-e:` image references
   - Updates the image tag to the just-built version using sed
   - Commits with git user.email=github@jedarden.com, user.name=jedarden
   - Pushes to origin

## Pattern Used

This follows the same pattern already in use by:
- telegram-claude-bridge-build (handles multiple images)
- news-trader-build (single image, like duck-e)

## Problem Solved

The duck-e deployment on ardenone-cluster had been stuck on `ronaldraygun/duck-e:0.2.106` for 36+ days while the VERSION file in the duck-e repo had advanced to 0.2.124+ via duck-e-build CI runs. The workflow was building and pushing images but never updating the k8s manifest, so ArgoCD had nothing new to sync.

## Commit

```
e7ab88f1 ci(duck-e-build): add auto-deploy step to update declarative-config
```

Pushed to jedarden/declarative-config main branch.

## Result

Future duck-e-build runs will automatically update k8s/ardenone-cluster/ducke/ducke-deployment.yml with the new image tag, triggering an ArgoCD sync and deployment rollout.
