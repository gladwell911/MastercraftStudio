# Native Mine Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every forum Mine tab entry open a native screen and keep topic-like rows formatted like the forum home screen.

**Architecture:** Add server-backed API/store methods for Mine activity lists, bookmarks, drafts, account actions, and notification dismissal. Reuse and generalize existing native screens where possible; add small focused screens for account settings and admin entry.

**Tech Stack:** React Native class components, React Navigation, Jest, Detox, Discourse JSON APIs.

---

## File Structure
- Modify `js/forum/discourse_api.js`: API methods and normalizers.
- Modify `js/forum/forum_store.js`: cache-backed store methods and authenticated writes.
- Modify `js/screens/ForumUserActivityScreen.js`: support `route.params.activityType`, `title`, and matching store method.
- Create `js/screens/ForumAccountSettingsScreen.js`: native username/password forms.
- Create `js/screens/ForumAdminScreen.js`: native admin entry screen.
- Modify `js/screens/ForumMineScreen.js`: route all rows natively.
- Modify `js/screens/index.js` and `js/Discourse.js`: register new routes.
- Modify tests in `js/forum/__tests__`, `js/screens/__tests__`, and `e2e/native_forum.test.js`.

## Task 1: API and Store Coverage
- [ ] Write failing tests in `js/forum/__tests__/discourse_api.test.js` for `fetchUserActivityTopics`, `fetchUserBookmarks`, `fetchDrafts`, `dismissUnreadNotifications`, `changeUsername`, and `changePassword`.
- [ ] Write failing tests in `js/forum/__tests__/forum_store.test.js` for authenticated cache/write behavior.
- [ ] Run `corepack yarn jest js/forum/__tests__/discourse_api.test.js js/forum/__tests__/forum_store.test.js --runInBand --testPathIgnorePatterns=.worktrees` and confirm expected failures.
- [ ] Implement minimal API/store methods.
- [ ] Re-run the same Jest command and confirm pass.

## Task 2: Native Activity Lists
- [ ] Write failing tests in `js/screens/__tests__/ForumUserScreens.test.js` showing `ForumUserActivityScreen` loads topics/replies/read/bookmarks/drafts according to `activityType` and renders `HomeTopicRow`.
- [ ] Run the focused test and confirm failure.
- [ ] Generalize `ForumUserActivityScreen` to dispatch to the right store method.
- [ ] Re-run focused test and confirm pass.

## Task 3: Account and Admin Native Screens
- [ ] Add failing screen tests for username/password forms and native admin entry.
- [ ] Run focused screen tests and confirm failure.
- [ ] Create `ForumAccountSettingsScreen.js` and `ForumAdminScreen.js`.
- [ ] Register routes in `js/screens/index.js` and `js/Discourse.js`.
- [ ] Re-run focused tests and confirm pass.

## Task 4: Mine Navigation
- [ ] Update `ForumMineScreen.test.js` so every Mine row and subrow asserts `navigation.navigate(...)` and `openUrl` is never called.
- [ ] Run the Mine test and confirm failure.
- [ ] Change `ForumMineScreen` row handlers to native routes.
- [ ] Re-run Mine test and confirm pass.

## Task 5: E2E Coverage
- [ ] Update `e2e/native_forum.test.js` to navigate from Mine to a native activity list, account settings, admin, notifications, and profile where authenticated state allows it.
- [ ] Run local Jest suite for touched tests.
- [ ] Run targeted eslint on touched files.
- [ ] Run Android build if Detox requires a fresh app.
- [ ] Run at least one Android Detox end-to-end test covering Mine native navigation.
