<StackPanel>
    <StackPanel.Resources>
        <!-- 全局字体：MiSans（现代 UI 字体）。带回退，缺失时退回雅黑 -->
        <Style TargetType="TextBlock">
            <Setter Property="FontFamily" Value="MiSans, Microsoft YaHei UI, Segoe UI" />
        </Style>

    </StackPanel.Resources>
    <!-- __BANNER__ -->
    <!-- CanSwap=False 表示不可折叠；不带 IsSwapped（教程要求它必须搭配 CanSwap=True） -->
    <local:MyCard Title="欢迎" Margin="0,0,0,16" CanSwap="False">
        <StackPanel Margin="25,40,23,16">
            <StackPanel.Triggers>
                <EventTrigger RoutedEvent="StackPanel.Loaded">
                    <BeginStoryboard>
                        <Storyboard>
                            <DoubleAnimation Storyboard.TargetProperty="Opacity"
                                             From="0" To="1" Duration="0:0:0.90"
                                             BeginTime="0:0:0.30">
                                <DoubleAnimation.EasingFunction>
                                    <CubicEase EasingMode="EaseOut" />
                                </DoubleAnimation.EasingFunction>
                            </DoubleAnimation>
                            <DoubleAnimation
                                Storyboard.TargetProperty="(UIElement.Effect).(BlurEffect.Radius)"
                                From="8" To="0" Duration="0:0:0.90" BeginTime="0:0:0.30" />
                        </Storyboard>
                    </BeginStoryboard>
                </EventTrigger>
            </StackPanel.Triggers>
            <StackPanel.Effect>
                <BlurEffect Radius="0" />
            </StackPanel.Effect>
            <!-- __FESTIVAL_BANNER__ -->
            <Border CornerRadius="12" Height="280" Margin="0,0,0,16" ClipToBounds="True">
                <Grid>
                    <local:MyImage Source="{{WALLPAPER_URL|url}}" HorizontalAlignment="Stretch" VerticalAlignment="Stretch" Stretch="UniformToFill" />
                    <Border>
                        <Border.Background>
                            <LinearGradientBrush StartPoint="0,0" EndPoint="0,1">
                                <GradientStop Color="#33000000" Offset="0" />
                                <GradientStop Color="#88000000" Offset="0.55" />
                                <GradientStop Color="#CC000000" Offset="1" />
                            </LinearGradientBrush>
                        </Border.Background>
                    </Border>
                    <Border HorizontalAlignment="Left" VerticalAlignment="Top" Margin="18,16,0,0" Background="#59000000" CornerRadius="12" Padding="12,10,16,10">
                        <StackPanel>
                            <StackPanel Orientation="Horizontal">
                                <Border Width="26" Height="26" CornerRadius="13" Background="{DynamicResource ColorBrush1}" Margin="0,0,8,0" VerticalAlignment="Center">
                                    <local:MyImage Width="16" Height="16" HorizontalAlignment="Center" VerticalAlignment="Center" Source="pack://application:,,,/images/Blocks/Grass.png" />
                                </Border>
                                <TextBlock Text="__GREETING__，{user}！" FontSize="15" FontWeight="Bold" Foreground="White" VerticalAlignment="Center" />
                            </StackPanel>
                            <TextBlock Text="__GREETING_SUB__" FontSize="11" Foreground="#D9FFFFFF" Margin="0,3,0,0" />
                        </StackPanel>
                    </Border>
                    <!-- __COUNTDOWN_BODY__ -->
                    <StackPanel VerticalAlignment="Center" HorizontalAlignment="Center">
                        <StackPanel Orientation="Horizontal" HorizontalAlignment="Center">
                            <TextBlock Text="__DATE_MONTH__" FontSize="52" FontWeight="Bold" Foreground="White" />
                            <TextBlock Text=" 月 " FontSize="15" VerticalAlignment="Bottom" Margin="0,0,4,16" Foreground="#D9FFFFFF" />
                            <TextBlock Text="__DATE_DAY__" FontSize="52" FontWeight="Bold" Foreground="White" />
                            <TextBlock Text=" 日" FontSize="15" VerticalAlignment="Bottom" Margin="0,0,4,16" Foreground="#D9FFFFFF" />
                        </StackPanel>
                        <TextBlock Text="星期__DATE_WEEKDAY__" HorizontalAlignment="Center" FontSize="13" FontWeight="Bold" Foreground="#FFFFFF" Margin="0,10,0,0" />
                        <!-- 农历（uapis.cn 的 lunartime；接口不可用时退回本地换算，都没有则整行消失） -->
                        <!-- __LUNAR__ -->
                    </StackPanel>
                    <!-- 每日一言（叠加在横幅底部，全站同一句，按天更新；见 saying.py） -->
                    <StackPanel VerticalAlignment="Bottom" Margin="20,0,16,12" HorizontalAlignment="Center">
                        <StackPanel Orientation="Horizontal" HorizontalAlignment="Center" Margin="0,0,0,4">
                            <Border Width="3" Height="10" CornerRadius="1.5" Background="#FFFFFF" Margin="0,0,8,0" VerticalAlignment="Center" />
                            <TextBlock Text="每日一言" FontSize="11" FontWeight="Bold" Foreground="#D9FFFFFF" VerticalAlignment="Center" />
                        </StackPanel>
                        <TextBlock Text="__QUOTE__" FontSize="12" Foreground="#D9FFFFFF" TextWrapping="Wrap" TextAlignment="Center" HorizontalAlignment="Center" MaxWidth="540" LineHeight="22" />
                    </StackPanel>
                </Grid>
            </Border>
            <!-- 天气卡片：原来放"幸运数字 / 幸运颜色"的位置，由请求期插入 -->
            <!-- __WEATHER_BODY__ -->
            <!-- 功能按钮（原本在"你的信息"卡片里，那张卡已移除）。启用 AI 时构建期
                 会多塞一个「AI 分析」入口，所以整排由下面的占位符生成。
                 注意：这里的注释不能写出占位符本身，否则会被替换一遍塞进注释里 -->

{{ACTION_BUTTONS}}
        </StackPanel>
    </local:MyCard>
    <!-- 功能网站：替代原来的"你的信息"卡片，条目与图标由构建期写入（见 config.DEFAULT_SITES） -->
    <local:MyCard Title="功能网站" Margin="0,0,0,16" CanSwap="False">
        <StackPanel Margin="25,40,23,16">
{{SITE_ITEMS}}
        </StackPanel>
    </local:MyCard>
</StackPanel>
