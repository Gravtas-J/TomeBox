class EventBus:
    """A lightweight, centralized Publish/Subscribe event bus."""

    def __init__(self):
        # Dictionary mapping topics to a list of callable handlers
        self._subscribers = {}

    def subscribe(self, topic: str, handler: callable):
        """Registers a callback function to a specific topic."""
        if topic not in self._subscribers:
            self._subscribers[topic] = []

        if handler not in self._subscribers[topic]:
            self._subscribers[topic].append(handler)

    def unsubscribe(self, topic: str, handler: callable):
        """Removes a registered callback from a specific topic."""
        if topic in self._subscribers and handler in self._subscribers[topic]:
            self._subscribers[topic].remove(handler)
            # Clean up the topic key if no subscribers are left
            if not self._subscribers[topic]:
                del self._subscribers[topic]

    def publish(self, topic: str, **kwargs):
        """Fires an event, broadcasting the kwargs to all registered handlers."""
        if topic not in self._subscribers:
            return

        for handler in self._subscribers[topic]:
            handler(**kwargs)


# Global singleton instance for the application to share
default_bus = EventBus()
