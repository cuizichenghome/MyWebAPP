"""
Default configurations.
"""

configs = {
    'debug': True,
    'db': {
        'host': "127.0.0.1",
        "port": 3306,
        "user": "cc",
        "password": "password",
        "db": 'myblog'
    },
    "session": {
        "secret": 'MyBlog'
    }
}