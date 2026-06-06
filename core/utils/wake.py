try:
    from wakepy import keep
except ImportError:

    class KeepDummy:
        def running(self):
            class ContextDummy:
                def __enter__(self):
                    pass

                def __exit__(self, *args):
                    pass

            return ContextDummy()

    keep = KeepDummy()
