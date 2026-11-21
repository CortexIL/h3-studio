# Projects: shared folders of clips

**Date:** 2026-11-21 · **Status:** agreed, not built

## What the user asked for

> as for now each user has their own panel with clips that they created i want to
> change it to be project based. lets say im working on a new project im making a
> video for school i want to click create project and then inside the project i
> have the ui for this specific project i can see all of the videos ive created for
> this specific project organized also im able to invite another person to my
> project and he can view or even create videos inside that project so we'll be
> able to work together

## Decisions (asked and answered 2026-11-21)

1. **Invites go to existing accounts only.** The owner types an email. If an
   account with that email exists, that person is added at once and the project
   appears in their list. If not, the owner is told to ask an admin to create the
   account. Accounts are still made only by an admin.
2. **Editors watch, download and redo anyone's clip.** Editors make new clips and
   may "Run again", enlarge or continue any clip in the project. Only the person
   who made a clip, or the project's owner, may delete, cancel, retry, edit or
   remove it from the feed.
3. **Old clips: one project each.** Every person who has clips gets one project
   called "My clips" holding all of them, and owns it. Nothing moves between people.
4. **The Archive moves inside each project.** A project has two tabs: Studio (make
   clips and watch them progress) and Clips (the finished videos, with search,
   select and download). The top menu shows Projects instead of Studio and Archive.

## Rules that follow

- **Three roles:** `owner` (one per project, its creator), `editor` (shown as "Can
  make videos"), `viewer` (shown as "Can watch").
- **What each role may do:**

  | | viewer | editor | owner |
  |---|---|---|---|
  | See the project, its feed, its clips; watch, download | ✓ | ✓ | ✓ |
  | Make clips, run again, enlarge, continue, drop a batch | | ✓ | ✓ |
  | Delete / cancel / retry / edit / hide **own** clips | | ✓ | ✓ |
  | Do that to **other people's** clips | | | ✓ |
  | Rename or delete the project; add, remove, change people | | | ✓ |
  | Leave the project | ✓ | ✓ | (cannot; deletes instead) |

- **Not a member: 404**, for the project and for every clip in it. A member who
  lacks the right gets 403. They can already see the thing, so a 403 reveals nothing.
- **Fair sharing of the GPUs stays per person**, as it is today. Projects do not
  change whose turn it is.
- **Deleting a project** deletes its clips and their files for everyone. The app
  refuses while any clip in it is waiting or being made.
- **Older browser tabs keep working** after the deploy. A request with no project
  behaves as it does today, and a new clip sent without a project goes to the
  sender's "My clips" project.
- Interface text in English and Hebrew follows the plain-language rule: say what
  you get and what to do, with no jargon.

## Not in this change

Moving clips between projects, handing ownership to someone else, invites to
people without an account, notifications, per-project budgets.
