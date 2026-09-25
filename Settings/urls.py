from django.conf import settings
from django.conf.urls.static import static
from django.http import HttpResponse
from django.urls import include, path, re_path
from django.views.static import serve


def healthz(_request):
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    #path('admin/', admin.site.urls),
    #path('', include('Swiggy.urls')),
    path('healthz/', healthz),
    path('support/', include('CustomerSupport.urls')),
]

urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT
    )
# if settings.DEBUG:
#     urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
#     urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / 'static')

