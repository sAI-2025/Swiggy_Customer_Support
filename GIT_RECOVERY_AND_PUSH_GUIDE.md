# Git Recovery and Push Guide

This document explains what happened when `git reset --hard origin/Deployment` was used, how the lost work was recovered, why GitHub blocked the push, and how the final branch was successfully pushed.

## 1. What Happened

The command below was run on the local `Deployment` branch:

```bash
git reset --hard origin/Deployment
```

That command moved the local branch back to the commit that already existed on GitHub and also reset the working tree.

Important detail: if your work had already been saved in local commits, Git may still be able to recover it using the reflog.

## 2. How the Lost Commit Was Recovered

The recovery started by checking Git history and the reflog.

### Commands used

```bash
git status --short --branch
git reflog -n 10 --date=iso
git log --oneline --decorate -n 5
```

### Why reflog helped

`git reflog` records where `HEAD` has been recently, even after resets. That means it can show commits that are no longer visible in the normal branch history.

In this case, the reflog showed the commit before the reset, so the branch could be restored to that commit.

### Recovery command

```bash
git reset --hard 56e4563
```

That moved the branch back to the recovered commit and restored the files exactly as they existed in that commit.

## 3. Why the Push Failed

After the recovery, the push to GitHub failed because GitHub Push Protection detected a secret in the branch history.

The error was a `GH013` repository rule violation.

### Problem source

The secret was inside the notebook file:

```text
Swiggy/Agent/Swiggy.ipynb
```

### Why deleting the file later was not enough

GitHub scans the commits being pushed. If one of those commits still contains the secret, the push is blocked even if a later commit deletes the file.

So the fix had to remove the notebook from the push history, not just from the final file tree.

## 4. How the Push Problem Was Solved

The branch history was rewritten so the final push contained only clean changes.

### Step 1: Remove the notebook from the branch

```bash
git rm --force "Swiggy/Agent/Swiggy.ipynb"
git commit -m "Remove notebook from deployment branch"
```

That removed the file, but the push was still blocked because the earlier notebook commit was still in the history being pushed.

### Step 2: Rebuild the branch cleanly

```bash
git reset --soft origin/Deployment
git commit -m "Update food and grocery support flows"
```

`--soft` moved the branch pointer back to the remote base but kept the changes staged, so a new clean commit could be created without the notebook commit in the ancestry.

### Step 3: Push again

```bash
git push origin Deployment
```

This time the push succeeded.

## 5. What Each Command Means

### `git reset --hard origin/Deployment`

Moves the branch to the same commit as the remote branch and discards local working changes.

### `git reflog`

Shows recent positions of `HEAD`, including commits that are no longer visible in normal branch history.

### `git reset --hard <commit>`

Moves the branch, index, and working tree to the selected commit.

### `git rm --force <file>`

Removes a tracked file from Git, even if it is already staged or modified.

### `git reset --soft origin/Deployment`

Moves the branch pointer back while keeping file changes staged so they can be recommitted cleanly.

### `git commit -m "..."`

Creates a new commit from the staged changes.

### `git push origin Deployment`

Uploads the local `Deployment` branch to GitHub.

## 6. Final Result

The final state is:

- the lost work was recovered
- the notebook file was removed from the branch history
- the branch was rewritten into one clean commit
- the cleaned branch was pushed successfully to GitHub

## 7. Simple Beginner Summary

Think of the process like this:

1. `reset --hard` moved the project back.
2. `reflog` helped find the lost commit.
3. GitHub blocked the push because a secret was inside the notebook history.
4. The branch was rewritten so the notebook was no longer part of the pushed history.
5. The final push worked.

## 8. Commands Timeline

```bash
git reset --hard origin/Deployment
git reflog -n 10 --date=iso
git reset --hard 56e4563
git rm --force "Swiggy/Agent/Swiggy.ipynb"
git reset --soft origin/Deployment
git commit -m "Update food and grocery support flows"
git push origin Deployment
```

## 9. Important Lesson

When GitHub Push Protection blocks a push, deleting the file in a later commit is often not enough. You usually need to rewrite the branch so the secret never appears in the commit history being pushed.
