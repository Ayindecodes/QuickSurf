# core/middleware.py
class CaptureLoginActivity:
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        resp = self.get_response(request)
        # Record only on successful token obtain or session login
        return resp
