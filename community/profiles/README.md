# Community profiles

Thermostat profiles shared by Easy TRV users. Easy TRV lists them under
**Add thermostat profile → Download from the community** and installs one with
a click.

## Share a profile

1. In Easy TRV open your profile (*Show or edit profile*) and click the share
   link. It opens the [Share a thermostat profile](../../../../issues/new?template=profile.yml)
   form on GitHub with the profile filled in.
2. Submit the form. A workflow checks the profile and opens a pull request.
3. Once the pull request is merged, the profile is in the list for everyone.

## Files

- `<id>.yaml`: one profile each, in the format described in
  [docs/concept/profiles.md](../../docs/concept/profiles.md).
- `index.json`: the list Easy TRV reads. It is generated; run
  `uv run python scripts/build_community_index.py` after changing a file.

Profiles with `backend: entity` (Beta) drive the thermostat through its Home
Assistant entities and work with any integration.
