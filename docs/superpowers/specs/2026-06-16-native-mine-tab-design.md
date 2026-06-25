# Native Mine Tab Design

## Goal
Every actionable entry in the native forum Mine tab opens a native React Native screen instead of a WebView, while topic-like lists render with the same row format used by the forum home screen.

## Scope
- Convert Mine tab entries for posted topics, replies, read topics, bookmarks, drafts, admin, username change, password change, notification/account subentries, profile, and notification dismissal to native navigation.
- Reuse existing native screens for messages, notifications, moderation, and user profile where they already match the entry.
- Add native screens only where existing screens cannot represent the feature.
- Keep WebView out of Mine entry handlers. External links inside unrelated flows are out of scope.

## Server API Mapping
- Posted topics, replies, and read topics use `/user_actions.json?username=<username>&filter=<ids>`.
- Bookmarks use `/u/<username>/bookmarks.json`.
- Drafts use `/drafts.json`.
- Notifications use `/notifications.json`; dismiss unread notifications uses `PUT /notifications/read`.
- Messages use `/topics/private-messages/<username>.json`.
- Review queue uses `/review.json`.
- User profile uses `/u/<username>.json`.
- Username and password changes use `PUT /u/<username>/preferences/username` and `PUT /u/<username>/preferences/password`.
- Admin is represented by a native admin entry screen with links to native-capable admin functions available in this app today, primarily moderation/review.

## Client Architecture
- Extend `discourse_api.js` with normalizers for user action topics, bookmarks, drafts, full notifications, dismiss notifications, username change, and password change.
- Extend `forum_store.js` with cache-backed methods for Mine activity lists and authenticated write actions.
- Generalize `ForumUserActivityScreen` so it accepts an activity type and title, then loads the corresponding store method. It continues to render `Components.HomeTopicRow`.
- Add `ForumAccountSettingsScreen` for username/password forms.
- Add `ForumAdminScreen` for native admin options.
- Update `ForumMineScreen` to navigate to native routes for every row and subrow.

## Accessibility and UI Stability
- Lists remain `FlatList` with `accessible={false}` and accessible rows, matching existing forum screens.
- Background refreshes must not repaint list controls or change selection when there is no visible data change.
- The Mine screen should only update state for row expansion/login/user loading changes.

## Testing
- Add failing Jest tests first for API normalization, store methods, Mine navigation, activity screen loading, and account settings actions.
- Update Detox native forum flow to open several Mine entries and assert native screens, including at least one activity list and one account/admin page.
- Verification must include targeted Jest, lint for touched JS/e2e files, and at least one Android Detox end-to-end run.
