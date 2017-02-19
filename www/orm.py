# -*- coding:utf-8 -*-
import asyncio
import aiomysql
import logging
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s:%(levelname)s: %(message)s")

__author__ = 'CC'


def log(sql, args=()):
    logging.info('SQL: %s' % sql)

# 创建一个全局的连接池，每个http请求都从池中获得数据库连接
async def create_pool(loop, **kw):
    logging.info('create database connection pool......')
    # 全局__pool用于存储整个连接池
    global __pool
    __pool = await aiomysql.create_pool(
        # **kw参数可以包含所有连接需要用到的关键字参数
        # 默认本机IP
        host=kw.get('host', 'localhost'),
        port=kw.get('port', 3306),
        user=kw['user'],
        password=kw['password'],
        db=kw['db'],
        charset=kw.get('charset', 'utf8'),  # 注意是utf8
        autocommit=kw.get('autocommit', True),
        maxsize=kw.get('maxsize', 10),
        minsize=kw.get('minsize', 1),

        # 接收一个event_loop实例
        loop=loop
    )

# 封装SQL_SELECT语句为select函数
# 为什么不用commit提交？
async def select(sql, args, size=None):
    log(sql, args)
    global __pool
    async with __pool.get() as conn:
        # DictCursor is a cursor which returns as a dictionary
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # 替换占位符，执行SQL语句
                await cur.execute(sql.replace('?', '%s'), args or ())
                if size:
                    rs = await cur.fetchmany(size)
                else:
                    rs = await cur.fetchall()
        except BaseException:
            raise
        finally:
            conn.close()
        logging.info('rows returned: %s ' % len(rs))
        return rs

# 封装insert,update,delete语句，
# 三者操作参数一致，定义一个通用的执行函数，
# 返回操作影响的行号 execute只返回结果数，不返回结果集
async def execute(sql, args, autocommit=True):
    log(sql)
    async with __pool.get() as conn:
        if not autocommit:
            await conn.begin()
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql.replace('?', '%s'), args)
                affected = cur.rowcount
            if not autocommit:
                await conn.commit()
        except BaseException:
            if not autocommit:
                await conn.rollback()
                raise
        finally:
            conn.close()
        return affected


# 根据参数数量生成SQL占位符'?'列表，
def create_args_string(num):
    L = []
    for n in range(num):
        L.append('?')
    # 以', '为分隔符，将列表合成字符串
    return ', '.join(L)


# 定义Field类，负责保存（数据库）表的字段名和字段类型
class Field(object):
    # 表的字段包括名字，类型，是否为主键，默认值
    def __init__(self, name, column_type, primary_key, default):
        self.name = name
        self.column_type = column_type
        self.primary_key = primary_key
        self.default = default

    # 输出类名，字段类型和名字
    def __str__(self):
        return '<%s, %s:%s>' % (self.__class__.__name__, self.column_type, self.name)


# 定义不同类型的衍生Field，表的不同列的字段的类型不一样

class StringField(Field):
    # ddl表示数据定义语言，
    def __init__(self, name=None, primary_key=False, default=None, ddl='varchar(100)'):
        super().__init__(name, ddl, primary_key, default)


class BooleanField(Field):

    def __init__(self, name=None, default=False):
        super().__init__(name, 'boolean', False, default)


class IntegerField(Field):

    def __init__(self, name=None, primary_key=False, default=0):
        super().__init__(name, 'bigint', primary_key, default)


class FloatField(Field):

    def __init__(self, name=None, primary_key=False, default=0.0):
        super().__init__(name, 'real', primary_key, default)


class TextField(Field):

    def __init__(self, name=None, default=None):
        super().__init__(name, 'text', False, default)


# 定义Model的元类
# 所有的元类都继承自type，ModelMetaclass元类定义了所有Model基类（继承ModelMetaclass）的子类实现的操作

# ModelMetaclass的工作主要是为一个数据库表映射成一个封装的类做准备
# 读取具体子类（user）的映射信息
# 创建类时，排除对Model的修改
# 在当前类中查找所有的类属性（attrs），如果找到Field属性，就将其保存到__mappings__的dict中
# 同时从类属性中删除Field(防止实例属性遮住类的同名属性)
# 将数据库表名保存到__table__中

# 完成这些工作就可以在Model中定义各种数据库的操作方法
class ModelMetaclass(type):

    # __new__控制__init__的执行，所以在其执行之前
    # cls:代表要__init__的类，此参数在实例化时由Python解释器自动提供
    # bases: 代表继承父类的集合
    # atrrs: 类的方法集合
    def __new__(cls, name, bases, attrs):
        # 封锁对Model的修改
        if name == 'Model':
            return type.__new__(cls, name, bases, attrs)
        # 获取table名字
        tableName = attrs.get('__table__', None) or name
        logging.info('found model: %s (table: %s)' % (name, tableName))

        # 获取Field和主键名
        mappings = dict()
        fields = []
        primaryKey = None
        for k, v in attrs.items():
            # Filed属性
            if isinstance(v, Field):
                # 此处打印的k是类的一个属性，v是这个属性在数据库中对应的Field列表属性
                logging.info(' Found mapping: %s==> %s' % (k, v))
                mappings[k] = v
                if v.primary_key:
                    # 找到主键
                    if primaryKey:
                        raise BaseException('Duplicate primary key for field : %s' % k)
                    primaryKey = k
                else:
                    fields.append(k)
        if not primaryKey:
            raise BaseException('Primary key not found.')
        # 从类属性中删除Field属性，实例的属性会遮盖类的同名属性
        for k in mappings.keys():
            attrs.pop(k)
        # 保存除主键外的属性名为``（运算出字符串）列表形式
        escaped_fields = list(map(lambda f: '`%s`' % f, fields))
        # 保存属性和列的映射关系
        attrs['__mappings__'] = mappings
        # 保存表名
        attrs['__table__'] = tableName
        # 保存主键属性名
        attrs['__primary_key__'] = primaryKey
        # 保存除主键外的属性名
        attrs['__fields__'] = fields
        # 构造默认的SELECT、INSERT、UPDATE、DELETE语句
        # ``反引号功能同repr()
        attrs["__select__"] = 'select `%s`, %s from `%s` ' % (primaryKey, ', '.join(escaped_fields), tableName)
        attrs['__insert__'] = \
            'insert into `%s` (%s, `%s`) value (%s)' % \
            (tableName, ', '.join(escaped_fields), primaryKey, create_args_string(len(escaped_fields) + 1))
        attrs["__update__"] = \
            'update `%s` set %s where `%s`=? ' % \
            (tableName, ', '.join(map(lambda f: '`%s`=?' % (mappings.get(f).name or f), fields)), primaryKey)
        attrs['__delete__'] = 'delete from `%s` where `%s`=?' % (tableName, primaryKey)
        return type.__new__(cls, name, bases, attrs)


# 定义ORM所有映射的基类：Model
# Model类的任何子类可以映射为一个数据库表
# Model类可以看做是对所有数据库表操作的基本定义的映射

# 基于字典查询形式
# Model从dict继承，拥有字典的所有功能，同时实现特殊方法__getattr__和__setattr__，能够实现属性操作
# 实现数据库操作的所有方法，定义为class方法，所有继承自Model都具有数据库操作方法
class Model(dict, metaclass=ModelMetaclass):
    # 继承了字典，所以可以接受任意属性？ 实例取的是字典的值
    def __init__(self, **kw):
        super(Model, self).__init__(**kw)

    # _getattr_用于查询不在__dict__系统中的属性
    # __dict__分层存储属性，每一层的__dict__只存储每一层新加的属性。子类不需要重复存储父类的属性。
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(r'"Model" object has no attribute "%s" ' % key)

    def __setattr__(self, key, value):
        self[key] = value

    def getValue(self, key):
        return getattr(self, key, None)

    def getValueOrDefault(self, key):
        value = getattr(self, key, None)
        if value is None:
            field = self.__mappings__[key]
            if field.default is not None:
                value = field.default() if callable(field.default) else field.default
                logging.debug('using default value for %s : %s ' % (key, str(value)))
                setattr(self, key, value)
        return value

    @classmethod
    async def findAll(cls, where=None, args=None, **kw):
        """find object by where clause."""
        sql = [cls.__select__]
        if where:
            sql.append('where')
            sql.append(where)
        if args is None:
            args = []
        orderBy = kw.get('orderBy', None)  # 语句中是否有orderBy参数
        if orderBy:
            sql.append('order by')
            sql.append(orderBy)
        limit = kw.get('limit', None)  # 语句中是否有limit参数
        if limit is not None:
            sql.append('limit')
            if isinstance(limit, int):
                sql.append('?')
                args.append(limit)
            elif isinstance(limit, tuple) and len(limit) == 2:
                sql.append('?,?')
                args.extend(limit)
            else:
                raise ValueError('Invalid limit value: %s ' % str(limit))
        rs = await select(' '.join(sql), args)
        return [cls(**r) for r in rs]

    @classmethod
    async def findNumber(cls, selectField, where=None, args=None):
        """find number by select and where."""
        # 这里的 _num_ 为别名，任何客户端都可以按照这个名称引用这个列，就像它是个实际的列一样
        sql = ['select %s _num_ from `%s` ' % (selectField, cls.__table__)]

        if where:
            sql.append('where')
            sql.append(where)
        rs = await select(' '.join(sql), args, 1)
        if len(rs) == 0:
            return None
        # rs[0]表示一行数据,是一个字典，而rs是一个列表
        return rs[0]['_num_']

    @classmethod
    async def find(cls, pk):
        """find object by primary key."""
        rs = await select('%s where `%s`= ?' % (cls.__select__, cls.__primary_key__), [pk], 1)
        if len(rs) == 0:
            return None
        # 1.将rs[0]转换成关键字参数元组，rs[0]为dict
        # 2.通过<class '__main__.User'>(位置参数元组)，产生一个实例对象
        return cls(**rs[0])

    async def save(self):
        args = list(map(self.getValueOrDefault, self.__fields__))
        args.append(self.getValueOrDefault(self.__primary_key__))
        rows = await execute(self.__insert__, args)
        if rows != 1:
            logging.warning('Failed to insert record: affected rows: %s' % rows)

    async def update(self):
        args = list(map(self.getValue, self.__fields__))
        args.append(self.getValue(self.__primary_key__))
        rows = await execute(self.__update__, args)
        if rows != 1:
            logging.warning('Failed to update by primary key: affected rows: %s' % rows)

    async def remove(self):
        args = [self.getValue(self.__primary_key__)]
        rows = await execute(self.__delete__, args)
        if rows != 1:
            logging.warning('Failed to remove by primary key: affected rows: %s' % rows)
























