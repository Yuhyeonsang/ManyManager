import 'react-native-gesture-handler';
import React from 'react';
import { Pressable, Text, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';

import DashboardScreen from './src/screens/DashboardScreen';
import FavoritesScreen from './src/screens/FavoritesScreen';
import DetailScreen from './src/screens/DetailScreen';
import SearchScreen from './src/screens/SearchScreen';
import SettingsScreen from './src/screens/SettingsScreen';

const Stack = createNativeStackNavigator();
const Tab = createBottomTabNavigator();

const sharedHeaderOptions = {
  headerStyle: { backgroundColor: '#0F172A' },
  headerTintColor: '#F8FAFC',
  headerTitleStyle: { fontWeight: '700' },
  contentStyle: { backgroundColor: '#F1F5F9' },
};

function MainTabs() {
  return (
    <Tab.Navigator
      screenOptions={{
        ...sharedHeaderOptions,
        tabBarStyle: {
          backgroundColor: '#0F172A',
          borderTopColor: '#1E293B',
        },
        tabBarActiveTintColor: '#F8FAFC',
        tabBarInactiveTintColor: '#64748B',
        tabBarLabelStyle: { fontSize: 11, fontWeight: '600' },
      }}
    >
      <Tab.Screen
        name="Dashboard"
        component={DashboardScreen}
        options={({ navigation }) => ({
          title: '오늘의 핫 종목',
          tabBarLabel: '홈',
          tabBarIcon: ({ color: iconColor }) => (
            <Text style={{ fontSize: 18, color: iconColor }}>{'🔥'}</Text>
          ),
          headerRight: () => (
            <View style={{ flexDirection: 'row', gap: 16, marginRight: 4 }}>
              <Pressable
                onPress={() => navigation.navigate('Search')}
                hitSlop={12}
                style={({ pressed }) => [{ opacity: pressed ? 0.6 : 1 }]}
              >
                <Text style={{ color: '#F8FAFC', fontSize: 18 }}>{'🔍'}</Text>
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
      <Tab.Screen
        name="Favorites"
        component={FavoritesScreen}
        options={({ navigation }) => ({
          title: '관심종목',
          tabBarLabel: '관심',
          tabBarIcon: ({ color: iconColor }) => (
            <Text style={{ fontSize: 18, color: iconColor }}>{'⭐'}</Text>
          ),
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
    </Tab.Navigator>
  );
}

export default function App() {
  return (
    <NavigationContainer>
      <StatusBar style="light" />
      <Stack.Navigator screenOptions={sharedHeaderOptions}>
        <Stack.Screen
          name="Main"
          component={MainTabs}
          options={{ headerShown: false }}
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
      </Stack.Navigator>
    </NavigationContainer>
  );
}
