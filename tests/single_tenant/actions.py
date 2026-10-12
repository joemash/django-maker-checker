"""The action guarded in the single-tenant project: renaming a group."""

from django.contrib.auth.models import Group

from django_maker_checker.actions import approval_action

RENAME = "group_rename"


@approval_action(
    RENAME,
    label="Rename Group",
    category="Access",
    lock_key=lambda p: f"group:{p['group_id']}",
    target_label=lambda p: Group.objects.get(pk=p["group_id"]).name,
)
def rename_group(*, tenant, maker, context, group_id, name):
    Group.objects.filter(pk=group_id).update(name=name)
    return {"group_id": group_id}
