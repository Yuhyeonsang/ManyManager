import 'react-native-gesture-handler';
import React from 'react';
import { Pressable, Text, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';

import DashboardScreen from './src/screens/DashboardScreen';
import FavoritesScreen from './src/screens/FavoritesScreen';
import DetailScreen from './src/screens/DetailScreen';
import SearchScreen from './src/screens/SearchScreen';
import SettingsScreen from './src/screens/SettingsScreen';
import AutoTradeScreen from './src/screens/AutoTradeScreen';
import BacktestScreen from './src/screens/BacktestScreen';

const Stack = createNativeStackNavigator();

const headerOptions = {
  headerStyle: { backgroundColor: '#0F172A' },
  headerTintColor: '#F8FAFC',
  headerTitleStyle: { fontWeight: '700' },
  contentStyle: { backgroundColor: '#F1F5F9' },
};

export default function App() {
  return (
    <NavigationContainer>
      <StatusBar style="light" />
      <Stack.Navigator screenOptions={headerOptions}>
        <Stack.Screen
          name="Dashboard"
          component={DashboardScreen}
          options={({ navigation }) => ({
            title: '오늘의 핫 종목',
            headerRight: () => (
              <View style={{ flexDirection: 'row', gap: 16, marginRight: 4 }}>
                <Pressable
                  onPress={() => navigation.navigate('Favorites')}
                  hitSlop={12}
                  style={({ pressed }) => [{ opacity: pressed ? 0.6 : 1 }]}
                >
                  <Text style={{ color: '#F8FAFC', fontSize: 18 }}>{'⭐'}</Text>
                </Pressable>
                <Pressable
                  onPress={() => navigation.navigate('Search')}
                  hitSlop={12}
                  style={({ pressed }) => [{ opacity: pressed ? 0.6 : 1 }]}
                >
                  <Text style={{ color: '#F8FAFC', fontSize: 18 }}>{'🔍'}</Text>
                </Pressable>
                <Pressable
                  onPress={() => navigation.navigate('Backtest')}
                  hitSlop={12}
                  style={({ pressed }) => [{ opacity: pressed ? 0.6 : 1 }]}
                >
                  <Text style={{ color: '#F8FAFC', fontSize: 18 }}>{'📈'}</Text>
                </Pressable>
                <Pressable
                  onPress={() => navigation.navigate('AutoTrade')}
                  hitSlop={12}
                  style={({ pressed }) => [{ opacity: pressed ? 0.6 : 1 }]}
                >
                  <Text style={{ color: '#F8FAFC', fontSize: 18 }}>{'🤖'}</Text>
                </Pressable>
                <Pressable
                  onPress={() => navigation.navigate('Settings')}
                  hitSlop={12}
                  style={({ pressed }) => [{ opacity: pressed ? 0.6 : 1 }]}
                >
                  <Text style={{ color: '#F8FAFC', fontSize: 18 }}>{'⚙️'}</Text>
                </Pressable>
              </View>
            ),
          })}
        />
        <Stack.Screen
          name="Favorites"
          component={FavoritesScreen}
          options={({ navigation }) => ({
            title: '관심종목',
            headerRight: () => (
              <Pressable
                onPress={() => navigation.navigate('Search')}
                hitSlop={12}
                style={({ pressed }) => [{ opacity: pressed ? 0.6 : 1, marginRight: 4 }]}
              >
                <Text style={{ color: '#F8FAFC', fontSize: 18 }}>{'🔍'}</Text>
              </Pressable>
            ),
          })}
        />
        <Stack.Screen
          name="Search"
          component={SearchScreen}
          options={{ title: '종목 검색' }}
        />
        <Stack.Screen
          name="Detail"
          component={DetailScreen}
          options={({ route }) => ({
            title: route.params?.name ?? '상세 분석',
          })}
        />
        <Stack.Screen
          name="Settings"
          component={SettingsScreen}
          options={{ title: '서버 설정' }}
        />
        <Stack.Screen
          name="Backtest"
          component={BacktestScreen}
          options={{ title: '📈 백테스트' }}
        />
        <Stack.Screen
          name="AutoTrade"
          component={AutoTradeScreen}
          options={{ title: '🤖 자동매매' }}
        />
      </Stack.Navigator>
    </NavigationContainer>
  );
}
