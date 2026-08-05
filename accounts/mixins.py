from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.contrib import messages

class RoleRequiredMixin(LoginRequiredMixin):
    allowed_roles = []

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if self.allowed_roles and request.user.role not in self.allowed_roles:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


def is_modal_request(request):
    """True when the page was fetched by the app-wide modal JS (see
    base.html) instead of a normal full-page navigation. Public helper —
    use this directly in get()/post()/dispatch() overrides that redirect
    before form_valid() ever runs, so those early exits stay modal-aware
    too instead of just always doing a normal redirect."""
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


# kept as an alias so existing internal references keep working
_is_modal_request = is_modal_request


def bulk_delete(request, queryset, redirect_to, *redirect_args, item_label='record', **redirect_kwargs):
    """Generic "Delete Selected" POST handler shared by every list dashboard
    (Users, Class Rosters, Labs, Inventory, Notifications, ...) so bulk
    delete works the same way everywhere instead of each app reinventing
    it. Pairs with the checkbox column + floating toolbar rendered by
    templates/partials/bulk_delete_toolbar.html and static/js/bulk-delete.js.

    `queryset` must already be scoped to whatever that page's normal list
    view would show (its own permission/ownership/department filtering) —
    this only narrows it further to the checked rows, it never widens
    access. Only rows whose pk was posted as `selected_ids` are removed.

    Usage from a view (function-based; each app decides its own
    role/ownership check before calling this, same as its single-row
    delete view does):

        def users_bulk_delete(request):
            if not request.user.is_authenticated or request.user.role != 'admin':
                raise PermissionDenied
            return bulk_delete(
                request, User.objects.exclude(pk=request.user.pk),
                'users', item_label='user',
            )
    """
    from django.shortcuts import redirect
    from django.urls import reverse

    if request.method != 'POST':
        raise PermissionDenied

    ids = [v for v in request.POST.getlist('selected_ids') if v]
    if not ids:
        messages.error(request, 'No rows were selected — check at least one row first.')
    else:
        # Deletes one object at a time (rather than a single bulk
        # queryset.delete()) so that any model with a custom delete()
        # override — e.g. ClassRoster, which also cleans up its
        # still-pending auto-generated sessions — behaves exactly the same
        # whether it's removed one-by-one or via "Delete Selected".
        matched = list(queryset.filter(pk__in=ids))
        count = 0
        for obj in matched:
            obj.delete()
            count += 1
        if count:
            messages.success(request, f'Deleted {count} {item_label}{"s" if count != 1 else ""}.')
        else:
            messages.error(request, 'None of the selected rows could be deleted (they may no longer exist, or are outside what you\'re allowed to remove).')

    if is_modal_request(request):
        return JsonResponse({'success': True, 'redirect': reverse(redirect_to, args=redirect_args, kwargs=redirect_kwargs)})
    return redirect(redirect_to, *redirect_args, **redirect_kwargs)


def modal_redirect(request, view_name, *args, **kwargs):
    """Drop-in replacement for `redirect(view_name, *args, **kwargs)` in a
    plain function-based view, for "action" endpoints (approve/reject/
    resolve/archive/etc.) that a form inside a modal-opened detail page
    posts to. A normal redirect() would get transparently followed by the
    modal's AJAX fetch, dumping the *entire* destination page (sidebar,
    navbar and all) into the modal instead of just closing it. This
    returns JSON telling the modal to close and refresh instead, when the
    request came from the modal — and the normal redirect otherwise."""
    from django.shortcuts import redirect
    from django.urls import reverse
    if is_modal_request(request):
        return JsonResponse({'success': True, 'redirect': reverse(view_name, args=args, kwargs=kwargs)})
    return redirect(view_name, *args, **kwargs)


class ModalFormMixin:
    """Mix into a CreateView/UpdateView (or any FormView) so it can render
    inside the app-wide "add / edit" modal (base.html) as well as work as
    a normal full page when visited directly.

    - GET via the modal: renders only the {% block content %} portion
      (no sidebar/navbar), by swapping which template it extends.
    - POST via the modal, success: returns JSON {"success": true} so the
      JS can close the modal and refresh the page behind it.
    - POST via the modal, validation errors: re-renders the bare form
      (status 200) so the JS can swap it back into the modal with the
      errors shown, instead of following a redirect/reload.
    """

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if is_modal_request(self.request):
            context['base_template'] = 'partials/bare.html'
            context['is_modal'] = True
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        if is_modal_request(self.request):
            return JsonResponse({'success': True, 'redirect': self.get_success_url()})
        return response

    def form_invalid(self, form):
        response = super().form_invalid(form)
        if is_modal_request(self.request):
            response.status_code = 200
        return response


class ModalDetailMixin:
    """Mix into a TemplateView/DetailView-style "view" page so it can be
    opened inside the app-wide modal instead of navigating to a separate
    page, while still working as a normal full page when visited directly.
    """

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if is_modal_request(self.request):
            context['base_template'] = 'partials/bare.html'
            context['is_modal'] = True
        return context