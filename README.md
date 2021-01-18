## Disc track splitter

把整轨音乐文件分割成分轨AAC。

依赖命令行程序：
- 7z : Used to unpack rar/7z/zip archives.
- convert (imagemagick)
- fdkaac
- mp4art (libmp4v2) : Only if you use `--embed_cover_art` option.
- shntool
- tar : Used to unpacket tar archives.

依赖库：
- absl-py
- natsort
- pillow

更多用法参考`./split.py --help`： 

    ./split.py:
        --input: The source folder or archive file containing the cue, audio and cover.
        --output: The audio files will be put in "<output>/<performer>/<album_name>"

例如：

    ./split.py -i downloaded.zip -o 'aac'

会生成`aac/艺术家名/专辑名/01.曲目名.m4a`文件
