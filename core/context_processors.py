from .models import Empresa


def empresa(request):
    if not request.user.is_authenticated:
        return {}
    return {'empresa': Empresa.actual()}
