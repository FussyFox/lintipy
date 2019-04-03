import sys


if __name__ == '__main__':
    try:
        if sys.argv[1] == '--version':
            print('1.2.3')
            exit(0)
    except KeyError:
        pass
    exit(1)
